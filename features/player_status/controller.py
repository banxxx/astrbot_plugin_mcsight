import asyncio
import logging
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image as AstrImage
from .image_generator import draw_multi_server_image
from .checker import fetch_from_plugin, query_one, query_all_servers, query_via_api
from ...config.whitelist_config import WhitelistManager

logger = logging.getLogger("astrbot_plugin_mcsight")

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
    plugin_api_port = wm.plugin_api_port  # 从配置读取端口

    # 如果启用服务端插件 API，则并发请求每个服务器的 API
    if use_plugin_api:
        tasks = []
        for srv in servers:
            host = srv["host"]
            # 构建 API URL，传入端口
            api_url = build_plugin_api_url(host, plugin_api_port)
            if not api_url:
                # 如果 host 是 self 或无效，跳过该服务器
                continue
            tasks.append(fetch_from_plugin(api_url, timeout=5.0))
        if tasks:
            plugin_results = await asyncio.gather(*tasks, return_exceptions=True)

            # 检查是否有任何成功结果，如果有则使用插件数据
            all_success = all(
                isinstance(r, list) and len(r) > 0 for r in plugin_results
            )
            if all_success:
                merged_servers = []
                for srv_data in plugin_results:
                    merged_servers.extend(srv_data)
                standardized = []
                for srv in merged_servers:
                    standardized.append({
                        "name": srv.get("name", "未知"),
                        "host": srv.get("host", ""),
                        "online": srv.get("online", 0),
                        "max": srv.get("max", 0),
                        "version": srv.get("version", "未知"),
                        "latency": srv.get("latency", 0.0),
                        "error": srv.get("error"),
                        "players": [
                            {
                                "name": p.get("name"),
                                "is_premium": p.get("is_premium")
                            }
                            for p in srv.get("players", [])
                        ]
                    })
                try:
                    img = await draw_multi_server_image(standardized)
                    img.save("mc_status_temp.png")
                    yield event.chain_result([AstrImage(file="mc_status_temp.png")])
                    return
                except Exception as e:
                    logger.error(f"生成图片失败: {e}")
                    yield event.plain_result(f"生成图片失败: {e}")
                    return
            else:
                logger.info("部分或全部插件 API 请求失败，回退到 mcstatus")

    # 回退到原有逻辑（mcstatus 或模组 API）
    async def fetch_server_data(srv):
        host = srv["host"]
        if use_mod_api:
            api_data = await query_via_api(host, mod_api_port)
            if api_data:
                return api_data
        return await query_one(srv)

    raw_results = await asyncio.gather(*[fetch_server_data(s) for s in servers])

    standardized = []
    for raw in raw_results:
        if "server" in raw and "players" in raw:
            standardized.append(standardize_from_api(raw))
        else:
            standardized.append(standardize_from_ping(raw))

    try:
        img = await draw_multi_server_image(standardized)
        img.save("mc_status_temp.png")
        yield event.chain_result([AstrImage(file="mc_status_temp.png")])
    except Exception as e:
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