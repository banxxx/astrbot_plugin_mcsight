"""绑定码验证的每用户失败预算：短时间内连续拿无效码来问，就不再替他去转发。

判据是「码确实不存在/已过期」这一种结果，与防抖（utils/debounce）无关：防抖按
（会话, 用户, 子命令）限频率，这里限的是无效尝试的数量。之所以要有这一层，是因为模组
的防爆破按**出口 IP** 记账（TokenRateLimiter），而所有 QQ 用户的 /绑定 都从同一个机器人
IP 出去 —— 一个人刷无效码会把全群共用的额度打光，且各服独立封 5 分钟。
"""
import math
import time
from astrbot.api import logger

# 统计窗口（秒）：窗口内的无效次数
WINDOW = 60.0
MAX_INVALID = 5
# 超上限后不再替他转发的时长（秒）
PENALTY = 60.0

# 键含用户，长跑必须给上限；到期回收 + 超限丢最旧
_MAX_KEYS = 4096
_PRUNE_AGE = 600.0
_SWEEP_INTERVAL = 256.0

# (会话, 用户) -> {"count": 窗口内无效次数, "window_start": 单调时钟, "blocked_until": 单调时钟}
_state = {}
_last_sweep = 0.0


def _sweep(now: float) -> None:
    global _last_sweep
    if now - _last_sweep < _SWEEP_INTERVAL and len(_state) <= _MAX_KEYS:
        return
    _last_sweep = now
    for key in [k for k, v in _state.items()
                if now - v["window_start"] > _PRUNE_AGE and v["blocked_until"] < now]:
        del _state[key]
    if len(_state) > _MAX_KEYS:
        # 海量不同用户来刷：丢最旧的一半保证有界，代价是那批人重新获得预算
        overflow = len(_state) - _MAX_KEYS // 2
        for key, _ in sorted(_state.items(), key=lambda kv: kv[1]["window_start"])[:overflow]:
            del _state[key]
        logger.warning(f"[bind_guard] 记录数超上限，已回收 {overflow} 条最旧记录")


def remaining(scope: str, user_id: str) -> int:
    """还需等待的秒数；0 表示可以替他转发。冷却到期即清账，重新给满额度。"""
    key = (scope, user_id)
    now = time.monotonic()
    _sweep(now)

    rec = _state.get(key)
    if rec is None:
        return 0
    if rec["blocked_until"] > now:
        return math.ceil(rec["blocked_until"] - now)
    if rec["blocked_until"] > 0:
        # 上一轮冷却已结束
        _state.pop(key, None)
    return 0


def record_invalid(scope: str, user_id: str) -> None:
    """记一次「模组判定码无效/已过期」。落在窗口外则重开窗口。"""
    key = (scope, user_id)
    now = time.monotonic()
    _sweep(now)

    rec = _state.get(key)
    if rec is None or now - rec["window_start"] > WINDOW:
        _state[key] = {"count": 1, "window_start": now, "blocked_until": 0.0}
        return

    rec["count"] += 1
    if rec["count"] >= MAX_INVALID and rec["blocked_until"] <= now:
        rec["blocked_until"] = now + PENALTY
        logger.warning(f"[bind_guard] {scope}/{user_id} 在 {WINDOW:.0f} 秒内 "
                       f"{rec['count']} 次无效绑定码，{PENALTY:.0f} 秒内不再替他转发")


def clear(scope: str, user_id: str) -> None:
    """拿到过有效码就把账清掉：预算只为连续瞎猜的人保留。"""
    _state.pop((scope, user_id), None)


def reset() -> None:
    """清空全部记账（测试用）"""
    global _last_sweep
    _state.clear()
    _last_sweep = time.monotonic()
