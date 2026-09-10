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
    """构建插件 API URL（已废弃，保留仅作参考）"""
    if not host or host == "self":
        return None
    if ":" in host:
        ip, _ = host.split(":", 1)
    else:
        ip = host
    return f"http://{ip}:{port}/api/status"

def standardize_from_api(data: dict) -> dict:
    """标准化插件 API 数据（已废弃）"""
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
    """标准化 mcstatus 数据"""
    result["extra"] = {}
    return result

# ========== 在线玩家状态查询 ==========
async def run_player_status(event: AstrMessageEvent, config_manager):
    """获取在线玩家状态，统一使用模组 API，若失败则回退到 mcstatus"""
    servers = config_manager.get_all_servers()
    if not servers:
        yield event.plain_result("还没有添加任何服务器。")
        return

    wm = WhitelistManager()
    token = wm.mod_api_token
    standardized = []

    for srv in servers:
        name = srv["name"]
        host = srv["host"]

        # ---- 判断是否安装了模组 ----
        # 没有 api_port 字段的服务器视为未安装模组，直接使用 mcstatus 查询
        if not wm.has_mod_api(srv):
            logger.info(f"服务器 {name} 未配置 api_port，视为未安装模组，使用 mcstatus 查询。")
            raw = await query_one(srv)
            standardized.append(standardize_from_ping(raw))
            continue

        port = wm.get_server_port(srv)

        # ---- 请求模组 API ----
        api_url = build_mod_api_url(host, port, "/api/status")
        mod_data = None
        if api_url:
            try:
                headers = {}
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url, headers=headers, timeout=5.0) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("success"):
                                mod_data = data.get("data", {})
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
                "last_activity_time": mod_data.get("last_activity_time", 0),
                "last_activity_player": mod_data.get("last_activity_player", ""),
                "players": [
                    {"name": p.get("name"), "uuid": p.get("uuid"), "is_premium": p.get("is_premium", True)}
                    for p in mod_data.get("players", [])
                ]
            })
        else:
            # 回退到 mcstatus
            raw = await query_one(srv)
            standardized.append(standardize_from_ping(raw))

    # 生成图片
    try:
        show_last_online = config_manager.is_last_online_enabled()
        img = await draw_multi_server_image(standardized, show_last_online=show_last_online)
        img.save("mc_status_temp.png", optimize=True, compress_level=9)
        yield event.chain_result([AstrImage(file="mc_status_temp.png")])
    except Exception as e:
        logger.error(f"生成图片失败: {e}")
        yield event.plain_result(f"生成图片失败: {e}")

# ========== 玩家统计数据查询 ==========
async def run_player_stats(event: AstrMessageEvent, config_manager, player_name: str, target_server: str = None):
    """查询玩家统计数据，统一使用模组 API"""
    if not player_name:
        yield event.plain_result("请指定玩家名称，例如：/mc stats 玩家名")
        return

    wm = WhitelistManager()
    token = wm.mod_api_token
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
        if not wm.has_mod_api(target_srv):
            yield event.plain_result(f"服务器「{target_server}」未安装模组，无法查询统计数据。")
            return
        targets = [target_srv]
    else:
        targets = [s for s in servers if wm.has_mod_api(s)]
        if not targets:
            yield event.plain_result("没有安装模组的服务器，无法查询统计数据。")
            return

    # ---- 统一使用模组 API ----
    for srv in targets:
        host = srv["host"]
        port = wm.get_server_port(srv)
        api_url = build_mod_api_url(host, port, f"/api/stats/{player_name}")
        if not api_url:
            continue
        try:
            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"
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