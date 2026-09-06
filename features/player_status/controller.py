import asyncio
import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image as AstrImage
from .image_generator import draw_multi_server_image
from .checker import (
    fetch_from_plugin, query_one, query_all_servers, query_via_api,
    fetch_player_stats, ping_server,
    build_mod_api_url, fetch_from_mod_api,
    send_broadcast_via_mod, fetch_tps_via_mod
)
from ...config.whitelist_config import WhitelistManager
from .player_stats_generator import draw_player_stats_image
from ...utils.avatar_cache import download_avatar

# ========== 工具函数 ==========
def build_plugin_api_url(host: str, port: int) -> str:
    if not host or host == "self":
        return None
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

# ========== 在线玩家状态查询 ==========
async def run_player_status(event: AstrMessageEvent, config_manager):
    """获取在线玩家状态，优先使用模组 API，若未启用则使用插件 API 或 mcstatus"""
    servers = config_manager.get_all_servers()
    if not servers:
        yield event.plain_result("还没有添加任何服务器。")
        return

    wm = WhitelistManager()
    use_mod_api = wm.enable_mod_api
    mod_api_port = wm.mod_api_port
    mod_token = wm.mod_api_token
    use_plugin_api = wm.use_plugin_api
    plugin_api_port = wm.plugin_api_port

    standardized = []

    for srv in servers:
        name = srv["name"]
        host = srv["host"]

        # ---- 优先：模组 API ----
        mod_data = None
        if use_mod_api:
            api_url = build_mod_api_url(host, mod_api_port, "/api/status")
            if api_url:
                try:
                    headers = {}
                    if mod_token:
                        headers["Authorization"] = f"Bearer {mod_token}"
                    async with aiohttp.ClientSession() as session:
                        async with session.get(api_url, headers=headers, timeout=5.0) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                if data.get("success"):
                                    response_data = data.get("data", {})
                                    mod_data = response_data
                except Exception as e:
                    logger.warning(f"模组 API 请求异常: {e}")

        if mod_data:
            standardized.append({
                "name": name,
                "host": host,
                "online": mod_data.get("online_players", 0),
                "max": mod_data.get("max_players", 0),
                "version": mod_data.get("version", "未知"),
                "latency": mod_data.get("latency", 0.0),
                "error": None,
                "players": [
                    {"name": p.get("name"), "uuid": p.get("uuid"), "is_premium": p.get("is_premium", True)}
                    for p in mod_data.get("players", [])
                ]
            })
            continue

        # ---- 旧逻辑：插件 API ----
        plugin_data = None
        if use_plugin_api:
            api_url = build_plugin_api_url(host, plugin_api_port)
            if api_url:
                try:
                    plugin_result = await fetch_from_plugin(api_url, timeout=5.0)
                    if plugin_result and isinstance(plugin_result, list) and len(plugin_result) > 0:
                        plugin_data = plugin_result[0]
                except Exception as e:
                    logger.warning(f"服务器 {name} 插件请求异常: {e}")

        if plugin_data:
            ping_task = asyncio.create_task(ping_server(host))
            try:
                real_latency = await asyncio.wait_for(ping_task, timeout=3.0)
                if real_latency > 0:
                    plugin_data["latency"] = real_latency
            except Exception:
                pass
            standardized.append({
                "name": plugin_data.get("name", name),
                "host": plugin_data.get("host", host),
                "online": plugin_data.get("online", 0),
                "max": plugin_data.get("max", 0),
                "version": plugin_data.get("version", "未知"),
                "latency": plugin_data.get("latency", 0.0),
                "error": plugin_data.get("error"),
                "players": [
                    {"name": p.get("name"), "uuid": p.get("uuid"), "is_premium": p.get("is_premium", True)}
                    for p in plugin_data.get("players", [])
                ]
            })
        else:
            # 最终回退到 mcstatus
            raw = await query_one(srv)
            standardized.append(standardize_from_ping(raw))

    # 生成图片
    try:
        img = await draw_multi_server_image(standardized)
        img.save("mc_status_temp.png", optimize=True, compress_level=9)
        yield event.chain_result([AstrImage(file="mc_status_temp.png")])
    except Exception as e:
        logger.error(f"生成图片失败: {e}")
        yield event.plain_result(f"生成图片失败: {e}")

# ========== 玩家统计数据查询 ==========
async def run_player_stats(event: AstrMessageEvent, config_manager, player_name: str, target_server: str = None):
    """查询玩家统计数据，优先使用模组 API，若未启用则使用插件 API"""
    if not player_name:
        yield event.plain_result("请指定玩家名称，例如：/mc stats 玩家名")
        return

    wm = WhitelistManager()
    use_mod_api = wm.enable_mod_api
    mod_api_port = wm.mod_api_port
    mod_token = wm.mod_api_token
    use_plugin_api = wm.use_plugin_api
    plugin_api_port = wm.plugin_api_port

    # 如果既没启用模组 API 也没启用插件 API，则直接报错
    if not use_mod_api and not use_plugin_api:
        yield event.plain_result("未启用任何玩家数据查询方式，请检查配置。")
        return

    servers = config_manager.get_all_servers()
    if not servers:
        yield event.plain_result("还没有添加任何服务器。")
        return

    found_data = None
    found_server_name = None
    all_servers_with_data = []

    # 确定目标服务器列表
    if target_server:
        target_srv = next((s for s in servers if s["name"] == target_server), None)
        if not target_srv:
            yield event.plain_result(f"未找到名为「{target_server}」的服务器，请检查配置。")
            return
        targets = [target_srv]
    else:
        targets = servers

    # ---- 步骤1：优先使用模组 API ----
    if use_mod_api:
        for srv in targets:
            host = srv["host"]
            api_url = build_mod_api_url(host, mod_api_port, f"/api/stats/{player_name}")
            if not api_url:
                continue
            try:
                headers = {}
                if mod_token:
                    headers["Authorization"] = f"Bearer {mod_token}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url, headers=headers, timeout=5.0) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("success"):
                                response_data = data.get("data", {})
                                if response_data:
                                    found_data = response_data
                                    found_server_name = srv["name"]
                                    all_servers_with_data.append((found_server_name, data))
                                    break  # 找到第一个有效数据即跳出
                        else:
                            logger.warning(f"模组 stats API 返回非 200: {resp.status}")
            except Exception as e:
                logger.warning(f"请求模组 stats 失败: {e}")
                continue

    # ---- 步骤2：如果模组 API 未返回数据且启用了插件 API，则使用旧逻辑 ----
    if found_data is None and use_plugin_api:
        # ---- 完整保留原有插件 API 逻辑 ----
        if target_server:
            # 指定服务器的情况（单服务器）
            target_srv = targets[0]
            host = target_srv["host"]
            api_url = build_plugin_api_url(host, plugin_api_port)
            if not api_url:
                yield event.plain_result(f"服务器「{target_server}」的地址无效。")
                return
            base_url = api_url.replace("/api/status", "")
            try:
                data = await asyncio.wait_for(
                    fetch_player_stats(base_url, player_name, timeout=3.0),
                    timeout=3.5
                )
            except asyncio.TimeoutError:
                yield event.plain_result(f"服务器「{target_server}」响应超时，请检查服务器是否在线。")
                return
            except Exception as e:
                yield event.plain_result(f"查询服务器「{target_server}」时出错: {e}")
                return

            if data is None:
                yield event.plain_result(
                    f"无法连接到服务器「{target_server}」的插件服务，请确认插件已安装且端口可访问。"
                )
                return
            if data.get('error'):
                yield event.plain_result(f"服务器「{target_server}」返回错误：{data.get('error')}")
                return
            found_data = data
            found_server_name = target_srv["name"]
            all_servers_with_data.append((found_server_name, data))
        else:
            # 未指定服务器：并发请求所有服务器
            tasks = []
            for srv in servers:
                host = srv["host"]
                api_url = build_plugin_api_url(host, plugin_api_port)
                if not api_url:
                    continue
                base_url = api_url.replace("/api/status", "")
                task = asyncio.create_task(
                    asyncio.wait_for(
                        fetch_player_stats(base_url, player_name, timeout=3.0),
                        timeout=3.5
                    )
                )
                tasks.append((srv["name"], task))

            valid_results = []
            error_results = []

            for name, task in tasks:
                try:
                    data = await task
                    if data is not None:
                        if data.get('error'):
                            error_results.append((name, data.get('error')))
                        else:
                            valid_results.append((name, data))
                except asyncio.TimeoutError:
                    error_results.append((name, "请求超时"))
                except Exception as e:
                    error_results.append((name, str(e)))

            if valid_results:
                found_server_name, found_data = valid_results[0]
                all_servers_with_data = valid_results
            else:
                if error_results:
                    name, err_msg = error_results[0]
                    yield event.plain_result(f"服务器「{name}」返回错误：{err_msg}")
                else:
                    yield event.plain_result(
                        f"未找到玩家 {player_name} 的统计数据。"
                        "可能原因：玩家未曾进入任何已配置的服务器，或所有服务器均未响应。"
                    )
                return

    # 如果所有方式都失败
    if found_data is None:
        yield event.plain_result(f"未找到玩家 {player_name} 的统计数据。")
        return

    # 获取头像并生成图片
    async with aiohttp.ClientSession() as session:
        avatar = await download_avatar(session, player_name, 72, is_premium=None, uuid=None)

    is_online = found_data.get('online', False)
    try:
        img = await draw_player_stats_image(
            found_data,
            player_name,
            server_name=found_server_name,
            is_online=is_online,
            avatar_img=avatar,
            show_server_label=(len(servers) > 1)
        )
        img.save("player_stats_temp.png", optimize=True, compress_level=9)
        yield event.chain_result([AstrImage(file="player_stats_temp.png")])
    except Exception as e:
        logger.error(f"生成玩家统计图片失败: {e}")
        yield event.plain_result(f"生成图片失败: {e}")

    # 多服务器提示
    if not target_server and len(all_servers_with_data) > 1:
        other_servers = [name for name, _ in all_servers_with_data if name != found_server_name]
        msg = f"检测到玩家 {player_name} 在多个服务器有数据，当前展示的是「{found_server_name}」的数据。\n"
        msg += f"如需查看其他服务器（{', '.join(other_servers)}）的数据，请使用命令：/mc stats {player_name} -s <服务器名称>"
        yield event.plain_result(msg)