import asyncio
import logging
from mcstatus import JavaServer
from typing import List, Dict, Any
import aiohttp
import json
from typing import Dict, Any, Optional

logger = logging.getLogger("astrbot_plugin_mcsight")

async def fetch_from_plugin(api_url: str, timeout: float = 5.0) -> Optional[List[Dict[str, Any]]]:
    """
    请求服务端插件的 /api/status 接口，返回服务器状态列表。
    如果请求失败，返回 None。
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{api_url}/api/status", timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("success"):
                        return data.get("servers", [])
                    else:
                        logger.warning(f"插件 API 返回错误: {data.get('error')}")
                        return None
                else:
                    logger.warning(f"插件 API 返回非 200 状态码: {resp.status}")
                    return None
    except asyncio.TimeoutError:
        logger.warning("请求插件 API 超时")
        return None
    except Exception as e:
        logger.error(f"请求插件 API 异常: {e}")
        return None

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

async def query_via_api(host: str, port: int, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """
    尝试连接模组 HTTP API，自动拼接 URL。
    host: 服务器 IP 或域名
    port: API 监听端口（全局统一）
    """
    url = f"http://{host}:{port}/api/players"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception:
        pass
    return None