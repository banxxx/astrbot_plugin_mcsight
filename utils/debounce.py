"""命令防抖：同一（会话, 用户, 子命令）在一个固定窗口内只放行一次。

与内核的 platform_settings.rate_limit 互补：那个按会话统计所有消息、超了就把流水线
静默按住（stall），不分命令也不区分成本；这里只管本插件的命令，按命令成本分档，
并且立刻回一句「请 X 秒后再试」。
"""
import math
import time
from astrbot.api import logger

# 三档窗口（秒），判据是一次命令要付出什么：
# HEAVY 要打全部服务器 + 画图 + 传图；MEDIUM 出图或跨境库往返；LIGHT 本地读写为主
WINDOW_HEAVY = 5.0
WINDOW_MEDIUM = 3.0
WINDOW_LIGHT = 1.0

HEAVY_CMDS = {"status", "stats", "tps", "checknick"}
# help/bindhelp 命中缓存不出网，但每次都是一条图片消息
MEDIUM_CMDS = {"bind", "unbind", "check", "say", "help", "bindhelp"}

# 键含用户，长跑必须给上限；到期回收 + 超限丢最旧
_MAX_KEYS = 4096
_PRUNE_AGE = 600.0
_SWEEP_INTERVAL = 256.0

_last_pass = {}
_warned = {}
_last_sweep = 0.0


def window_of(sub_cmd: str) -> float:
    if sub_cmd in HEAVY_CMDS:
        return WINDOW_HEAVY
    if sub_cmd in MEDIUM_CMDS:
        return WINDOW_MEDIUM
    return WINDOW_LIGHT


def _sweep(now: float) -> None:
    global _last_sweep
    if now - _last_sweep < _SWEEP_INTERVAL and len(_last_pass) <= _MAX_KEYS:
        return
    _last_sweep = now
    for table in (_last_pass, _warned):
        for key in [k for k, t in table.items() if t < now - _PRUNE_AGE]:
            del table[key]
        if len(table) > _MAX_KEYS:
            # 有人在用海量不同用户刷：丢最旧的一半保证有界，代价是那批人免一次冷却
            overflow = len(table) - _MAX_KEYS // 2
            for key, _ in sorted(table.items(), key=lambda kv: kv[1])[:overflow]:
                del table[key]
            logger.warning(f"[debounce] 记录数超上限，已回收 {overflow} 条最旧记录")


def gate(scope: str, user_id: str, sub_cmd: str) -> tuple:
    """判定并记账：返回 (是否放行, 剩余秒数)。窗口从上次放行时刻起算（固定窗口）。"""
    key = (scope, user_id, sub_cmd)
    now = time.monotonic()
    _sweep(now)

    window = window_of(sub_cmd)
    passed_at = _last_pass.get(key)
    if passed_at is not None and now - passed_at < window:
        return False, math.ceil(passed_at + window - now)

    _last_pass[key] = now
    _warned.pop(key, None)
    return True, 0


def should_notify(scope: str, user_id: str, sub_cmd: str) -> bool:
    """本窗口内第一次被拒才提示；调用一次即占用提示位。连点的人刷不出第二条提示。"""
    key = (scope, user_id, sub_cmd)
    if _warned.get(key):
        return False
    _warned[key] = time.monotonic()
    return True


def reset() -> None:
    """清空全部记账（测试用）"""
    global _last_sweep
    _last_pass.clear()
    _warned.clear()
    _last_sweep = time.monotonic()
