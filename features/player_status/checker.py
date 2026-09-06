import asyncio
import aiohttp
from astrbot.api import logger
from mcstatus import JavaServer
from typing import List, Dict, Any, Optional
import json

# ========== 模组 API 辅助函数 ==========
def build_mod_api_url(host: str, port: int, endpoint: str) -> Optional[str]:
    """构建模组 API URL（不包含 Token）"""
    if not host or host == "self":
        return None
    if ":" in host:
        ip, _ = host.split(":", 1)
    else:
        ip = host
    return f"http://{ip}:{port}{endpoint}"

async def fetch_from_mod_api(api_url: str, token: str = "", timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """请求模组 API 并返回 JSON（自动添加 Authorization）"""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.warning(f"模组 API 返回非 200: {resp.status}")
                    return None
    except Exception as e:
        logger.warning(f"请求模组 API 失败: {e}")
        return None

async def send_broadcast_via_mod(host: str, port: int, token: str, message: str, timeout: float = 5.0) -> bool:
    url = build_mod_api_url(host, port, "/api/broadcast")
    if not url:
        return False
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"message": message}, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("success", False)
                return False
    except Exception:
        return False

async def fetch_tps_via_mod(host: str, port: int, token: str, timeout: float = 5.0) -> Optional[float]:
    url = build_mod_api_url(host, port, "/api/tps")
    if not url:
        return None
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("success") and "data" in data:
                        return data["data"].get("tps")
                return None
    except Exception:
        return None

# ========== 原有函数（保持不变） ==========
async def fetch_from_plugin(api_url: str, timeout: float = 5.0) -> Optional[List[Dict[str, Any]]]:
    """请求服务端插件的 /api/status 接口，返回服务器状态列表。"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
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

async def fetch_player_stats(api_base_url: str, player_name: str, timeout: float = 5.0):
    """从服务端插件获取玩家统计数据，返回数据字典或错误字典"""
    url = f"{api_base_url}/api/stats/{player_name}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("success"):
                        return data.get("data", {})      # 只返回 data 部分
                    else:
                        return {"error": data.get("message", "未知错误")}
                else:
                    return {"error": f"HTTP {resp.status}"}
    except asyncio.TimeoutError:
        return {"error": "查询超时，请稍后重试"}
    except aiohttp.ClientConnectorError as e:
        return {"error": f"无法连接到服务器（{e}）"}
    except Exception as e:
        return {"error": f"获取统计异常: {e}"}

async def query_one(server_info: dict) -> Dict[str, Any]:
    """查询单个 Minecraft 服务器的状态（使用 mcstatus）"""
    host = server_info["host"]
    name = server_info["name"]
    try:
        server = await asyncio.to_thread(JavaServer.lookup, host)
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

        need_query = False
        if online > 0:
            if not players:
                need_query = True
            elif len(players) < online:
                need_query = True

        if need_query:
            try:
                query_resp = await asyncio.wait_for(
                    asyncio.to_thread(server.query),
                    timeout=5.0
                )
                if query_resp.players and query_resp.players.names:
                    players = query_resp.players.names
                    online = query_resp.players.online
                    max_players = query_resp.players.max
                else:
                    logger.warning(f"Query 返回空列表，保留 SLP 数据")
            except Exception as qe:
                logger.warning(f"Query 协议失败 ({host}): {qe}，保留 SLP 数据")

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
    """尝试连接模组 HTTP API（旧版 /api/players，用于兼容）"""
    url = f"http://{host}:{port}/api/players"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception:
        pass
    return None

async def ping_server(host: str, timeout: float = 3.0) -> float:
    """通过 mcstatus 的 ping 方法获取服务器延迟（毫秒）"""
    try:
        server = await asyncio.to_thread(JavaServer.lookup, host)
        latency = await asyncio.wait_for(
            asyncio.to_thread(server.ping),
            timeout=timeout
        )
        return latency
    except Exception:
        return 0.0

async def send_broadcast(api_base_url: str, message: str, timeout: float = 5.0) -> bool:
    """向服务端插件发送广播请求（旧插件 API）"""
    url = f"{api_base_url}/api/broadcast"
    payload = {"message": message}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    return True
                else:
                    logger.warning(f"广播 API 返回非 200 状态码: {resp.status}")
                    return False
    except asyncio.TimeoutError:
        logger.warning("广播请求超时")
        return False
    except aiohttp.ClientConnectorError as e:
        logger.error(f"连接广播 API 失败: {url} - {e}")
        return False
    except Exception as e:
        logger.error(f"发送广播请求异常: {e}")
        return False

async def fetch_tps(api_base_url: str, timeout: float = 5.0) -> Optional[float]:
    """从服务端插件获取当前服务器的 TPS（旧插件 API）"""
    url = f"{api_base_url}/api/tps"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("tps")
                else:
                    logger.warning(f"获取 TPS 返回非 200: {resp.status}")
                    return None
    except asyncio.TimeoutError:
        logger.warning("获取 TPS 超时")
        return None
    except aiohttp.ClientConnectorError as e:
        logger.error(f"连接 TPS API 失败: {url} - {e}")
        return None
    except Exception as e:
        logger.error(f"获取 TPS 异常: {e}")
        return None