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
    查询单个 Minecraft 服务器的状态。
    优先使用 Server List Ping 协议，若玩家列表不完整则尝试 Query 协议。
    加入 5 秒超时，避免卡死。
    """
    host = server_info["host"]
    name = server_info["name"]
    try:
        server = await asyncio.to_thread(JavaServer.lookup, host)

        # 使用 Status 协议（SLP），并设置超时
        status = await asyncio.wait_for(
            asyncio.to_thread(server.status),
            timeout=5.0
        )

        players = []
        if status.players.sample:
            players = [p.name for p in status.players.sample]

        version = status.version.name if status.version else "未知"
        latency = status.latency if status.latency else 0.0
        online = status.players.online
        max_players = status.players.max

        # 判断玩家列表是否可能不完整
        # 条件：在线人数 > 0 且 SLP 返回的列表为空，或列表人数明显少于在线人数（通常在线>12时SLP最多返回12人）
        need_query = False
        if online > 0:
            if not players:
                need_query = True
            elif len(players) < online:
                # 当在线人数超过12人时，SLP 只能返回12个样本，此时尝试 Query 获取完整列表
                need_query = True

        if need_query:
            try:
                # 尝试 Query 协议获取完整玩家列表
                query_resp = await asyncio.wait_for(
                    asyncio.to_thread(server.query),
                    timeout=5.0
                )
                if query_resp.players and query_resp.players.names:
                    players = query_resp.players.names
                    online = query_resp.players.online
                    max_players = query_resp.players.max
                    logger.info(f"服务器 {host} 使用 Query 获取到 {len(players)} 名玩家")
                else:
                    logger.warning(f"Query 返回空列表，保留 SLP 数据")
            except Exception as qe:
                logger.warning(f"Query 协议失败 ({host}): {qe}，保留 SLP 数据")
                # 保留 SLP 数据

        return {
            "name": name,
            "host": host,
            "online": online,
            "max": max_players,
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