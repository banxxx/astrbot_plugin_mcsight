import asyncio
import ipaddress
import socket
import time
import aiohttp
from astrbot.api import logger
from mcstatus import JavaServer
from typing import List, Dict, Any, Optional
import json

from ...utils import mod_http
from ...config.server_config import clean_motd, effective_remark

# 状态卡片「延迟」胶囊的测速上界（裸 TCP 建连，见 ping_server）。健康服实测 26~45ms，
# 1.2 秒留了几十倍余量；这条命令的总时长等于最慢那台的测速，一台不应答就能拖满。
PING_TIMEOUT = 1.2

# SLP 查询预算与重试：服务器/转发端偶发在建连后立刻断开（Broken pipe / Connection reset），
# mcstatus 自带的 tries 是在同一条已断开的 socket 上重试，重试必然还是同一个错误。
STATUS_TIMEOUT = 5.0
STATUS_ATTEMPTS = 3
STATUS_RETRY_DELAY = 0.2
# 显式写了默认端口 25565 却连不上时，按域名的 SRV 记录再查一次的预算。
SRV_LOOKUP_TIMEOUT = 1.5
EXPLICIT_25565_TIMEOUT = 2.0
SRV_FALLBACK_TIMEOUT = 3.0

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
    """通过模组 API 发送广播，返回是否真正成功（业务层面）"""
    ok, _ = await mod_http.request(
        host, port, "/api/broadcast", method="POST",
        data={"message": message}, token=token, timeout=timeout)
    return ok

async def fetch_tps_via_mod(host: str, port: int, token: str, timeout: float = 5.0) -> Optional[float]:
    """通过模组 API 获取 TPS，返回 TPS 值或 None"""
    ok, payload = await mod_http.request(
        host, port, "/api/tps", method="GET", token=token, timeout=timeout)
    if not ok or not isinstance(payload, dict):
        return None
    data = payload.get("data") or {}
    return data.get("tps")

# ========== 原有函数（保留，但可能不再使用，保持兼容）==========
async def fetch_from_plugin(api_url: str, timeout: float = 5.0) -> Optional[List[Dict[str, Any]]]:
    """请求服务端插件的 /api/status 接口，返回服务器状态列表。（已废弃，保留仅作参考）"""
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
    """从服务端插件获取玩家统计数据，返回数据字典或错误字典（已废弃，保留仅作参考）"""
    url = f"{api_base_url}/api/stats/{player_name}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("success"):
                        return data.get("data", {})
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

def motd_from_status(status) -> str:
    """从 mcstatus 的 status 里取 MOTD 纯文本；老版本没有 to_plain() 就直接 str()"""
    motd = getattr(status, "motd", None)
    if motd is None:
        return ""
    to_plain = getattr(motd, "to_plain", None)
    return clean_motd(to_plain() if callable(to_plain) else str(motd))


def split_explicit_port(host: str):
    """把「域名:端口」拆成 (域名, 端口)；没写端口或端口不是数字时返回 (host, None)。"""
    base, sep, port_str = host.rpartition(":")
    if sep and port_str.isdigit():
        return base, int(port_str)
    return host, None


def describe_query_error(exc: BaseException) -> str:
    """把 socket 层的英文异常翻译成卡片上能看懂的一句话。"""
    if isinstance(exc, BrokenPipeError):
        return "连接被服务器中断（Broken pipe）"
    if isinstance(exc, ConnectionResetError):
        return "连接被服务器重置"
    if isinstance(exc, ConnectionRefusedError):
        return "服务器积极拒绝连接"
    return str(exc) or type(exc).__name__


def _status_once(server):
    """单次 SLP 查询，不让 mcstatus 在同一条 socket 上重试。"""
    try:
        return server.status(tries=1)
    except TypeError:
        # 老版本 mcstatus 没有 tries 关键字
        return server.status()


async def _lookup_server(address: str, timeout: float):
    return await asyncio.wait_for(
        asyncio.to_thread(JavaServer.lookup, address), timeout=timeout)


async def _status_with_retry(server, timeout: float):
    """在 timeout 预算内用新连接重试 SLP；连接被对端关闭后必须重新 lookup/status。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last_exc = None
    for attempt in range(STATUS_ATTEMPTS):
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_status_once, server), timeout=remaining)
        except (asyncio.TimeoutError, OSError) as e:
            last_exc = e
            if attempt + 1 < STATUS_ATTEMPTS:
                delay = min(STATUS_RETRY_DELAY * (attempt + 1),
                            max(0.0, deadline - loop.time()))
                if delay > 0:
                    await asyncio.sleep(delay)
    if last_exc is None:
        raise asyncio.TimeoutError()
    raise last_exc


async def _srv_alternative(host: str):
    """显式写了 25565 时，返回域名的 SRV 目标；没有 SRV 或地址是 IP 时返回 None。"""
    base, port = split_explicit_port(host)
    if port != 25565:
        return None
    try:
        ipaddress.ip_address(base)
        return None  # IP 没有 SRV，省掉一次 DNS
    except ValueError:
        pass
    try:
        return await _lookup_server(base, SRV_LOOKUP_TIMEOUT)
    except Exception:
        return None


async def fetch_status(host: str):
    """查询服务器 SLP 状态，连接级错误换新连接重试。

    显式写成「域名:25565」会让 mcstatus 跳过 SRV 查询（与 Minecraft 地址栏一致）。
    有些服真实端口在 SRV 记录里，25565 不是游戏端口，就会 Broken pipe / 拒绝连接；
    这种「写了默认端口」的配置在失败后按 SRV 再试一次，更符合用户本意。
    """
    server = await _lookup_server(host, STATUS_TIMEOUT)
    fallback = await _srv_alternative(host)
    if fallback is None or fallback.address == server.address:
        return await _status_with_retry(server, STATUS_TIMEOUT)

    # 有 SRV 备选时，先把显式端口的等待压短：多数情况下 25565 就是写错的，
    # 不该为了等一个丢包的端口把整张卡拖满 5 秒。
    try:
        return await _status_with_retry(server, EXPLICIT_25565_TIMEOUT)
    except (asyncio.TimeoutError, OSError) as first_exc:
        logger.info(
            f"服务器 {host} 的 25565 查询失败（{type(first_exc).__name__}: {first_exc}），"
            f"按其 SRV 记录改用 {fallback.address.host}:{fallback.address.port} 重试"
        )
        try:
            return await _status_with_retry(fallback, SRV_FALLBACK_TIMEOUT)
        except Exception:
            raise first_exc


async def query_one(server_info: dict) -> Dict[str, Any]:
    """查询单个 Minecraft 服务器的状态（使用 mcstatus）"""
    host = server_info["host"]
    name = server_info["name"]
    remark = effective_remark(server_info)
    try:
        status = await fetch_status(host)
        players = []
        if status.players.sample:
            players = [p.name for p in status.players.sample]
        version = status.version.name if status.version else "未知"
        latency = status.latency if status.latency else 0.0
        online = status.players.online
        max_players = status.players.max
        motd = motd_from_status(status)

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
            "remark": remark,
            "motd": motd,
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
            "name": name, "host": host, "remark": remark, "motd": "",
            "online": 0, "max": 0, "players": [],
            "version": "未知", "latency": 0.0,
            "error": "查询超时"
        }
    except Exception as e:
        _, explicit_port = split_explicit_port(host)
        hint = ("；若该服使用 SRV 记录，请把地址改成不带端口的域名"
                if explicit_port == 25565 else "")
        retried = (f"（已换新连接重试 {STATUS_ATTEMPTS} 次）"
                   if isinstance(e, OSError) else "")
        logger.error(
            f"查询服务器 {host} 失败{retried}: "
            f"{type(e).__name__}: {e}{hint}")
        return {
            "name": name, "host": host, "remark": remark, "motd": "",
            "online": 0, "max": 0, "players": [],
            "version": "未知", "latency": 0.0,
            "error": describe_query_error(e)
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

async def ping_server(host: str, timeout: float = PING_TIMEOUT) -> float:
    """用裸 TCP 建连往返测服务器网络延迟（毫秒）。

    不用 mcstatus.ping()：它发的是「只握手不成帧」的探测包，隧道/面板转发
    （如 minekuai）不回这个包、只对完整 status 查询应答，于是这类服永远测不到延迟。
    TCP 建连不要求服务器懂任何 MC 协议，能转发就能测，且天然约等于一个网络往返。

    先解析再计时：并发七台时 getaddrinfo 会排到几百毫秒，混进窗口就把 30ms 的
    RTT 报成 350ms。解析出的字面 IP 再建连不会再走一次 DNS。
    解析失败/连不上/超时一律 0.0，由调用方保留原始占位值。
    """
    ip, _, port_str = host.partition(":")
    if not ip or ip == "self":
        return 0.0
    try:
        port = int(port_str) if port_str else 25565
    except ValueError:
        return 0.0

    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            ip, port, type=socket.SOCK_STREAM)
    except Exception:
        return 0.0

    t0 = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(infos[0][4][0], port), timeout=timeout)
    except Exception:
        return 0.0
    rtt_ms = (time.perf_counter() - t0) * 1000
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass
    return rtt_ms

# 以下函数已废弃，保留仅作参考
async def send_broadcast(api_base_url: str, message: str, timeout: float = 5.0) -> bool:
    """向服务端插件发送广播请求（旧插件 API，已废弃）"""
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
    """从服务端插件获取当前服务器的 TPS（旧插件 API，已废弃）"""
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