import asyncio
import logging
from mcstatus import JavaServer
from typing import List, Dict, Any

logger = logging.getLogger("astrbot_plugin_mcwatcher")

async def query_one(server_info: dict) -> Dict[str, Any]:
    """
    查询单个 Minecraft 服务器的状态（仅使用 Server List Ping 协议）。
    加入 5 秒超时，避免卡死。
    """
    host = server_info["host"]
    name = server_info["name"]
    try:
        server = await asyncio.to_thread(JavaServer.lookup, host)

        # 只使用 Status 协议，并设置超时
        status = await asyncio.wait_for(
            asyncio.to_thread(server.status),
            timeout=5.0
        )

        players = []
        if status.players.sample:
            players = [p.name for p in status.players.sample]

        version = status.version.name if status.version else "未知"
        latency = status.latency if status.latency else 0.0

        return {
            "name": name,
            "host": host,
            "online": status.players.online,
            "max": status.players.max,
            "players": players,
            "version": version,
            "latency": latency,
            "error": None
        }

    except asyncio.TimeoutError:
        logger.warning(f"查询服务器 {host} 超时")
        return {
            "name": name, "host": host,
            "online": 0, "max": 0, "players": [],
            "version": "未知", "latency": 0.0,
            "error": "查询超时"
        }
    except Exception as e:
        logger.error(f"查询服务器 {host} 失败: {e}")
        return {
            "name": name, "host": host,
            "online": 0, "max": 0, "players": [],
            "version": "未知", "latency": 0.0,
            "error": str(e)
        }

async def query_all_servers(servers: List[dict]) -> List[Dict[str, Any]]:
    tasks = [query_one(s) for s in servers]
    return await asyncio.gather(*tasks)