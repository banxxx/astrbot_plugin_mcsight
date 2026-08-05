import asyncio
import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image as AstrImage
from .image_generator import draw_multi_server_image
from .checker import fetch_from_plugin, query_one, query_all_servers, query_via_api, fetch_player_stats, ping_server
from ...config.whitelist_config import WhitelistManager
from .player_stats_generator import draw_player_stats_image
from ...utils.avatar_cache import download_avatar

async def run_player_status(event: AstrMessageEvent, config_manager):
    """获取在线玩家状态，优先使用服务端插件 API"""
    servers = config_manager.get_all_servers()
    if not servers:
        yield event.plain_result("还没有添加任何服务器。")
        return

    wm = WhitelistManager()
    use_mod_api = wm.enable_mod_api
    mod_api_port = wm.mod_api_port
    use_plugin_api = wm.use_plugin_api
    plugin_api_port = wm.plugin_api_port

    standardized = []  # 最终用于图片渲染的数据

    # 对每个服务器独立处理
    for srv in servers:
        name = srv["name"]
        host = srv["host"]

        # 1. 如果启用插件 API，尝试从插件获取数据
        plugin_data = None
        if use_plugin_api:
            api_url = build_plugin_api_url(host, plugin_api_port)
            if api_url:
                try:
                    plugin_result = await fetch_from_plugin(api_url, timeout=5.0)
                    if plugin_result and isinstance(plugin_result, list) and len(plugin_result) > 0:
                        # 插件返回的是列表，取第一个（因为每个服务器独立，列表应只有一项）
                        plugin_data = plugin_result[0]
                except Exception as e:
                    logger.warning(f"服务器 {name} 插件请求异常: {e}")

        # 2. 如果插件数据有效，则使用它
        if plugin_data:
            # 并发获取该服务器的真实延迟
            ping_task = asyncio.create_task(ping_server(host))
            # 等待延迟结果（不阻塞主流程）
            try:
                real_latency = await asyncio.wait_for(ping_task, timeout=3.0)
                if real_latency > 0:
                    plugin_data["latency"] = real_latency
            except Exception:
                pass  # 保持原有延迟（0）

            # 标准化插件数据（与之前 standardized 的格式一致）
            standardized.append({
                "name": plugin_data.get("name", name),
                "host": plugin_data.get("host", host),
                "online": plugin_data.get("online", 0),
                "max": plugin_data.get("max", 0),
                "version": plugin_data.get("version", "未知"),
                "latency": plugin_data.get("latency", 0.0),
                "error": plugin_data.get("error"),
                "players": [
                    {
                        "name": p.get("name"),
                        "uuid": p.get("uuid"),   # 插件会返回 uuid
                        "is_premium": p.get("is_premium")
                    }
                    for p in plugin_data.get("players", [])
                ]
            })
        else:
            # 3. 插件失败，回退到 mcstatus 或模组 API
            # 使用原有的回退逻辑（模组 API 或 SLP）
            async def fetch_server_data(srv):
                if use_mod_api:
                    api_data = await query_via_api(host, mod_api_port)
                    if api_data:
                        return api_data
                return await query_one(srv)

            raw = await fetch_server_data(srv)
            if "server" in raw and "players" in raw:
                standardized.append(standardize_from_api(raw))
            else:
                standardized.append(standardize_from_ping(raw))

    # 所有服务器数据收集完毕，生成图片
    try:
        img = await draw_multi_server_image(standardized)
        img.save("mc_status_temp.png")
        yield event.chain_result([AstrImage(file="mc_status_temp.png")])
    except Exception as e:
        logger.error(f"生成图片失败: {e}")
        yield event.plain_result(f"生成图片失败: {e}")

def build_plugin_api_url(host: str, port: int) -> str:
    """
    根据 MC 服务器地址和服务端插件 API 端口构建 API URL。
    如果 host 为 'self' 或无效，返回 None。
    """
    if not host or host == "self":
        return None
    # 解析 IP 和端口
    if ":" in host:
        ip, _ = host.split(":", 1)
    else:
        ip = host
    return f"http://{ip}:{port}/api/status"

def standardize_from_api(data: dict) -> dict:
    srv = data.get("server", {})
    players = data.get("players", [])
    return {
        "name": srv.get("name", "未知"),
        "host": srv.get("host", ""),
        "online": srv.get("online_players", 0),
        "max": srv.get("max_players", 0),
        "players": [p["name"] for p in players],
        "version": srv.get("version", "未知"),
        "latency": data.get("latency", 0.0),
        "error": None,
        "extra": {
            "player_details": players,
            "tps": srv.get("tps"),
        }
    }

def standardize_from_ping(result: dict) -> dict:
    result["extra"] = {}
    return result

# ===== 玩家统计功能 =====
async def run_player_stats(event: AstrMessageEvent, config_manager, player_name: str, target_server: str = None):
    """
    查询玩家统计数据并生成图片
    """
    if not player_name:
        yield event.plain_result("请指定玩家名称，例如：/mc stats 玩家名")
        return

    wm = WhitelistManager()
    use_plugin_api = wm.use_plugin_api
    plugin_api_port = wm.plugin_api_port
    if not use_plugin_api:
        yield event.plain_result("未启用服务端插件API，无法查询玩家统计数据。")
        return

    # 获取服务器列表
    servers = config_manager.get_all_servers()
    if not servers:
        yield event.plain_result("还没有添加任何服务器。")
        return

    found_data = None
    found_server_name = None
    all_servers_with_data = []  # 存储 (server_name, data)

    # 如果要查询指定服务器
    if target_server:
        target_srv = None
        for srv in servers:
            if srv["name"] == target_server:
                target_srv = srv
                break
        if not target_srv:
            yield event.plain_result(f"未找到名为「{target_server}」的服务器，请检查配置。")
            return
        host = target_srv["host"]
        api_url = build_plugin_api_url(host, plugin_api_port)
        if not api_url:
            yield event.plain_result(f"服务器「{target_server}」的地址无效。")
            return
        base_url = api_url.replace("/api/status", "")
        data = await fetch_player_stats(base_url, player_name)
        if data is None:
            # 连接失败，可能是插件未安装或网络不通
            yield event.plain_result(
                f"无法连接到服务器「{target_server}」的插件服务"
                "请确认插件已安装且端口可访问。"
            )
            return
        if data.get('error'):
            # 插件返回错误（玩家未进服等）
            yield event.plain_result(f"服务器「{target_server}」未找到玩家 {player_name} 的统计数据。")
            return
        # 有效数据
        found_data = data
        found_server_name = target_srv["name"]
        all_servers_with_data.append((found_server_name, data))
    else:
        # 遍历所有服务器，取第一个有效数据
        for srv in servers:
            host = srv["host"]
            api_url = build_plugin_api_url(host, plugin_api_port)
            if not api_url:
                continue  # 跳过无效服务器
            base_url = api_url.replace("/api/status", "")
            data = await fetch_player_stats(base_url, player_name)
            if data is None:
                # 连接失败，跳过该服务器
                continue
            if data.get('error'):
                # 插件返回错误（玩家无数据），跳过该服务器
                continue
            # 有效数据
            all_servers_with_data.append((srv["name"], data))
            if found_data is None:
                found_data = data
                found_server_name = srv["name"]
                # 继续遍历以收集所有有数据的服务器（可选）
        if not found_data:
            yield event.plain_result(
                f"未找到玩家 {player_name} 的统计数据。"
                "可能原因：玩家未曾进入任何已配置的服务器，或服务器未安装插件。"
            )
            return

    # 获取玩家头像（异步）
    async with aiohttp.ClientSession() as session:
        avatar = await download_avatar(session, player_name, 72, is_premium=None, uuid=None)

    # 获取群组服务器数量
    servers = config_manager.get_all_servers()
    show_server_label = len(servers) > 1   # 只有多个服务器时才显示标签

    # 生成图片
    is_online = found_data.get('online', False)
    try:
        img = await draw_player_stats_image(
            found_data,
            player_name,
            server_name=found_server_name,
            is_online=is_online,
            avatar_img=avatar,
            show_server_label=show_server_label   # 传入标志
        )
        img.save("player_stats_temp.png")
        yield event.chain_result([AstrImage(file="player_stats_temp.png")])
    except Exception as e:
        logger.error(f"生成玩家统计图片失败: {e}")
        yield event.plain_result(f"生成图片失败: {e}")

    # 如果有多个服务器都有数据，且未指定 target_server，发送提示
    if not target_server and len(all_servers_with_data) > 1:
        other_servers = [name for name, _ in all_servers_with_data if name != found_server_name]
        msg = f"检测到玩家 {player_name} 在多个服务器有数据，当前展示的是「{found_server_name}」的数据。\n"
        msg += f"如需查看其他服务器（{', '.join(other_servers)}）的数据，请使用命令：/mc stats {player_name} -s <服务器名称>"
        yield event.plain_result(msg)