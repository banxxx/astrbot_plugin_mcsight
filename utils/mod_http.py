"""模组 HTTP API 统一请求层。

职责：复用全局 ClientSession、快速判定不可达主机、把故障分类成面向用户的文案，
原始异常与内网地址只写日志不外泄。
"""
import asyncio
import json
import time
from typing import Any, Optional, Tuple

import aiohttp
from astrbot.api import logger

# 单次请求总预算（秒）
TOTAL_TIMEOUT = 5.0
# TCP 建连预算（秒）：服务器离线或端口丢包时快速失败，不再吃满总预算。
# 0.5s 的依据：线上 6 台面板服实测建连 31~32ms，仍有 16 倍余量；连不上的主机成本从 1.5s 降到 0.5s。
CONNECT_TIMEOUT = 0.5
# 全部服务器共用的连接池上限
CONNECTOR_LIMIT = 32

# 死服熔断：同一个 ip:port 连续「建连层」失败到达阈值后，在冷却期内直接跳过请求，
# 不再为它付建连超时的钱。只对连不上（拒绝解析失败建连超时）计数；HTTP 400/404/5xx
# 与响应超时一律不计 —— 那是「在线但报错/正忙」，熔断它会把好服永久挡在门外。
BREAKER_FAIL_THRESHOLD = 3
BREAKER_COOLDOWN = 60.0

CAT_INVALID_HOST = "invalid_host"
CAT_REFUSED = "refused"
CAT_CONNECT_TIMEOUT = "connect_timeout"
CAT_TIMEOUT = "timeout"
CAT_UNAUTHORIZED = "unauthorized"
CAT_NOT_FOUND = "not_found"
CAT_SERVER_ERROR = "server_error"
CAT_BAD_RESPONSE = "bad_response"
CAT_DISCONNECTED = "disconnected"
CAT_UNSUPPORTED_METHOD = "unsupported_method"
CAT_SKIPPED = "breaker_skipped"
CAT_UNKNOWN = "unknown"

_USER_TEXT = {
    CAT_INVALID_HOST: "无效的服务器地址",
    CAT_REFUSED: "无法连接到该服务器（模组可能未启动或未开放 API 端口）",
    CAT_CONNECT_TIMEOUT: "连接该服务器超时（服务器可能已离线）",
    CAT_TIMEOUT: "该服务器响应超时（服务器可能正忙）",
    CAT_UNAUTHORIZED: "模组拒绝了鉴权请求（机器人 apiToken 与模组配置不一致）",
    # 该行文案被 commands/bind.py 的 _format_sync_failures 按 "404/不存在" 识别为「模组过旧」，改动需保留标记
    CAT_NOT_FOUND: "接口不存在 (HTTP 404)",
    CAT_SERVER_ERROR: "模组返回内部错误",
    CAT_BAD_RESPONSE: "模组返回了无法解析的响应",
    CAT_DISCONNECTED: "模组中断了连接",
    CAT_UNSUPPORTED_METHOD: "不支持的请求方法",
    CAT_UNKNOWN: "请求模组失败",
}

# 跳过文案带剩余秒数，单独模板；不含「404/不存在」，
# 因此在 bind.py 的失败分组里会正确落到「同步失败」而不是「模组过旧」
_SKIPPED_TEXT = "该服务器近期连续连不上，已暂时跳过（约 {seconds} 秒后自动重试）"

_session: Optional[aiohttp.ClientSession] = None
_session_loop = None

# "ip:port" -> {"fails": 连续建连失败次数, "retry_at": 单调时钟下的解禁时刻}
_breaker = {}


def _breaker_cooldown_left(key: str) -> Optional[float]:
    """返回该主机还需跳过的秒数；None 表示放行（包括冷却已到期、放一条去试探）"""
    rec = _breaker.get(key)
    if rec is None:
        return None
    left = rec["retry_at"] - time.monotonic()
    return left if left > 0 else None


def _breaker_note_connect_failure(key: str) -> None:
    rec = _breaker.setdefault(key, {"fails": 0, "retry_at": 0.0})
    rec["fails"] += 1
    if rec["fails"] >= BREAKER_FAIL_THRESHOLD:
        rec["retry_at"] = time.monotonic() + BREAKER_COOLDOWN
        logger.warning(
            f"[mod_http] {key} 连续 {rec['fails']} 次建连失败，"
            f"{BREAKER_COOLDOWN:.0f} 秒内的请求直接跳过"
        )


def _breaker_clear(key: str) -> None:
    """拿到任何 HTTP 响应就说明链路是活的，失败计数归零"""
    _breaker.pop(key, None)


def reset_breaker(key: str = None) -> None:
    """清空熔断状态；不传 key 则全清（配置改了、手动排查时用）"""
    if key is None:
        _breaker.clear()
    else:
        _breaker.pop(key, None)


async def get_session() -> aiohttp.ClientSession:
    """返回当前事件循环下的全局 Session；ClientSession 构造不含 await，检查与赋值之间不会被其它协程插入。"""
    global _session, _session_loop
    loop = asyncio.get_running_loop()
    if _session is not None and (_session.closed or _session_loop is not loop):
        stale = _session
        _session = None
        _session_loop = None
        try:
            await stale.close()
        except Exception:
            # 旧循环已关闭时无法回收，丢弃即可，不影响新 Session
            pass
    if _session is None:
        _session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=CONNECTOR_LIMIT),
            timeout=aiohttp.ClientTimeout(total=TOTAL_TIMEOUT, connect=CONNECT_TIMEOUT),
        )
        _session_loop = loop
    return _session


async def close_session() -> None:
    """插件卸载时释放连接池。"""
    global _session, _session_loop
    session, _session, _session_loop = _session, None, None
    if session is not None and not session.closed:
        try:
            await session.close()
        except Exception as e:
            logger.warning(f"[mod_http] 关闭模组请求 Session 失败: {e}")


def _log(category: str, target: str, detail: str) -> None:
    logger.warning(f"[mod_http] {category} {target} {detail}")


def _timeout_for(total: Optional[float]) -> Optional[aiohttp.ClientTimeout]:
    if total is None:
        return None
    return aiohttp.ClientTimeout(total=total, connect=min(CONNECT_TIMEOUT, total))


def _classify(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        # aiohttp 3.10+ 会把建连超时单独抛出 ConnectionTimeoutError
        if exc.__class__.__name__ == "ConnectionTimeoutError":
            return CAT_CONNECT_TIMEOUT
        return CAT_TIMEOUT
    if isinstance(exc, aiohttp.ClientConnectorError):
        return CAT_REFUSED
    if isinstance(exc, aiohttp.ServerDisconnectedError):
        return CAT_DISCONNECTED
    return CAT_UNKNOWN


async def request(host: str, port: Any, endpoint: str,
                  method: str = "GET", data: dict = None,
                  token: str = "", timeout: Optional[float] = None) -> Tuple[bool, Any]:
    """请求模组 API。

    成功返回 (True, 完整 JSON 字典)；失败返回 (False, 面向用户的简短文案)。
    异常原文、状态码、响应片段只进日志，不回传给聊天侧。
    """
    if not host or host == "self":
        return False, _USER_TEXT[CAT_INVALID_HOST]
    ip = host.split(":", 1)[0] if ":" in host else host
    url = f"http://{ip}:{port}{endpoint}"
    target = f"{ip}:{port}{endpoint}"
    key = f"{ip}:{port}"

    cooldown_left = _breaker_cooldown_left(key)
    if cooldown_left is not None:
        _log(CAT_SKIPPED, target, f"cooldown_left={int(cooldown_left)}s")
        return False, _SKIPPED_TEXT.format(seconds=int(cooldown_left) + 1)

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    kwargs = {"headers": headers}
    per_timeout = _timeout_for(timeout)
    if per_timeout is not None:
        kwargs["timeout"] = per_timeout

    upper = (method or "").upper()
    if upper == "POST":
        kwargs["json"] = data
    elif upper == "GET":
        if data is not None:
            kwargs["params"] = data
    else:
        return False, _USER_TEXT[CAT_UNSUPPORTED_METHOD]

    try:
        session = await get_session()
        send = session.post if upper == "POST" else session.get
        async with send(url, **kwargs) as resp:
            # 拿到响应即说明建连成功，熔断计数归零
            _breaker_clear(key)

            if resp.status == 404:
                _log(CAT_NOT_FOUND, target, f"status={resp.status}")
                return False, _USER_TEXT[CAT_NOT_FOUND]

            # 先读文本再自行解析：模组 5xx 或中间层返回 HTML 时不会抛 ContentTypeError，
            # 避免「服务端错误」被误报成「连不上」
            raw = await resp.text()

            if resp.status == 401 or resp.status == 403:
                _log(CAT_UNAUTHORIZED, target, f"status={resp.status}")
                return False, _USER_TEXT[CAT_UNAUTHORIZED]
            if resp.status >= 500:
                _log(CAT_SERVER_ERROR, target, f"status={resp.status} body={raw[:200]}")
                return False, f"{_USER_TEXT[CAT_SERVER_ERROR]} (HTTP {resp.status})"
            if resp.status != 200:
                _log(CAT_BAD_RESPONSE, target, f"status={resp.status} body={raw[:200]}")

            try:
                result = json.loads(raw)
            except ValueError:
                _log(CAT_BAD_RESPONSE, target, f"status={resp.status} body={raw[:200]}")
                return False, _USER_TEXT[CAT_BAD_RESPONSE]
            if not isinstance(result, dict):
                _log(CAT_BAD_RESPONSE, target, f"status={resp.status} body={raw[:200]}")
                return False, _USER_TEXT[CAT_BAD_RESPONSE]

            if resp.status == 200 and result.get("success") is True:
                return True, result

            # 业务错误文案由模组产生（不含内网地址），可原样展示
            return False, result.get("message") or f"API 返回错误 (HTTP {resp.status})"
    except Exception as e:
        category = _classify(e)
        if category in (CAT_REFUSED, CAT_CONNECT_TIMEOUT):
            _breaker_note_connect_failure(key)
        _log(category, target, f"{type(e).__name__}: {e}")
        return False, _USER_TEXT.get(category, _USER_TEXT[CAT_UNKNOWN])
