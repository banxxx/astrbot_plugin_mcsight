"""绑定码 → 所属服务器的短期路由缓存：重试不再问遍所有服。

模组只在内存里持有令牌，插件事先不知道某个码属于哪台，只能并发问一遍全服；每台不是
持有者的服务器都会把这次问话记成一次失败，而记账维度是机器人出口 IP（全体玩家共用）。
所以命中过一次之后，同一个码的后续问话应该只发给那一台；问了不算数的情况一律丢弃缓存、
回落到全服扇出，正确性优先。
"""
import time
from astrbot.api import logger

# 与模组令牌有效期一致（TokenManager 的令牌 5 分钟过期）
TTL = 300.0

_MAX_KEYS = 512
_PRUNE_AGE = 900.0
_SWEEP_INTERVAL = 256.0

# 码 -> (host, port, gameId, 过期时刻)
_route = {}
_last_sweep = 0.0


def _sweep(now: float) -> None:
    global _last_sweep
    if now - _last_sweep < _SWEEP_INTERVAL and len(_route) <= _MAX_KEYS:
        return
    _last_sweep = now
    for code in [c for c, v in _route.items() if v[3] < now]:
        del _route[code]
    if len(_route) > _MAX_KEYS:
        overflow = len(_route) - _MAX_KEYS // 2
        for code, _ in sorted(_route.items(), key=lambda kv: kv[1][3])[:overflow]:
            del _route[code]
        logger.warning(f"[bind_route] 记录数超上限，已回收 {overflow} 条最旧路由")


def remember(code: str, host: str, port: int, game_id: str) -> None:
    """记下（或续期）这个码由哪台服务器持有"""
    now = time.monotonic()
    _sweep(now)
    _route[code] = (host, int(port), game_id, now + TTL)


def get(code: str):
    """返回 (host, port, gameId)；无缓存或已过期返回 None"""
    now = time.monotonic()
    _sweep(now)
    rec = _route.get(code)
    if rec is None:
        return None
    if rec[3] <= now:
        _route.pop(code, None)
        return None
    return rec[0], rec[1], rec[2]


def drop(code: str) -> None:
    """作废这条路由：缓存那台说了不算、或返回的游戏ID与缓存不符时调用"""
    _route.pop(code, None)


def reset() -> None:
    """清空全部路由（测试用）"""
    global _last_sweep
    _route.clear()
    _last_sweep = time.monotonic()
