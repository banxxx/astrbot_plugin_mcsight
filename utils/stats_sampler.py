"""玩家统计每日采样（时间轴的驱动源）

按日历给每个"被查询过的人"落一份当日累计快照，/查询 才有真正的 7 天窗口可用。
名单取自快照表自身，服务器端点汇总自 group_configs/servers_*.json。
"""
import asyncio
import glob
import json
import os
import time
from datetime import datetime, timedelta
from typing import Dict, Tuple

from astrbot.api import logger

from ..config.whitelist_config import WhitelistManager
from . import mod_http, stats_snapshot

# 每天本地时间 04:37：避开整点，也和备份/其它服任务错开
SAMPLE_AT = (4, 37)
# 单轮总预算，到点放弃剩余目标，明天继续——不能让采样跨到白天有人在用
ROUND_BUDGET = 480.0
# 逐个请求之间的间隔：模组对在线玩家的取数要回主线程，并发会排队甚至超时
REQUEST_GAP = 0.3
# 单轮最多采多少人
MAX_TARGETS = 300

_task: asyncio.Task = None


def _server_endpoints() -> Dict[str, Tuple[str, int]]:
    """服务器名（小写）→ (host, port)。各群配置里同名服务器视为同一台。"""
    root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
    wm = WhitelistManager()
    out = {}
    for path in glob.glob(os.path.join(root, 'group_configs', 'servers_*.json')):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                servers = json.load(f).get("servers", [])
        except (OSError, ValueError) as e:
            logger.warning(f"[采样] 读取群配置失败 {os.path.basename(path)}: {e}")
            continue
        for srv in servers:
            name = str(srv.get("name", "")).strip().lower()
            if not name or not wm.has_mod_api(srv):
                continue
            out[name] = (srv["host"], wm.get_server_port(srv))
    return out


async def sample_once() -> None:
    """跑一轮采样。异常只在内部消化，绝不让它冒到定时器外面。"""
    roster = stats_snapshot.list_sampled()
    if not roster:
        logger.info("[采样] 快照表里还没有人，本轮跳过")
        return
    endpoints = _server_endpoints()
    if not endpoints:
        logger.warning("[采样] 没有任何配置了 API 端口的服务器，本轮跳过")
        return
    token = WhitelistManager().mod_api_token
    started = time.monotonic()
    ok = no_data = failed = skipped = 0
    for idx, (player, server) in enumerate(roster):
        if idx >= MAX_TARGETS:
            skipped += len(roster) - idx
            logger.warning(f"[采样] 超出单轮人数上限 {MAX_TARGETS}，剩余 {skipped} 个目标本轮放弃")
            break
        if time.monotonic() - started > ROUND_BUDGET:
            skipped += len(roster) - idx
            logger.warning(f"[采样] 触及单轮预算上限，剩余 {skipped} 个目标本轮放弃")
            break
        host_port = endpoints.get(str(server).strip().lower())
        if not host_port:
            skipped += 1
            continue
        host, port = host_port
        try:
            success, payload = await mod_http.request(
                host, port, f"/api/stats/{player}", method="GET", token=token)
        except Exception as e:
            failed += 1
            logger.debug(f"[采样] {player}@{server} 请求异常: {e}")
            await asyncio.sleep(REQUEST_GAP)
            continue
        data = payload.get("data") if success and isinstance(payload, dict) else None
        if data:
            stats_snapshot.save_snapshot(player, server, data)
            ok += 1
        elif success:
            no_data += 1
        else:
            failed += 1
        await asyncio.sleep(REQUEST_GAP)
    logger.info(f"[采样] 本轮 {len(roster)} 人：成功 {ok}，无数据 {no_data}，"
                f"失败 {failed}，跳过 {skipped}，耗时 {time.monotonic() - started:.0f} 秒")


def _seconds_until_next_run() -> float:
    now = datetime.now()
    target = now.replace(hour=SAMPLE_AT[0], minute=SAMPLE_AT[1],
                         second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _loop() -> None:
    while True:
        wait = _seconds_until_next_run()
        logger.info(f"[采样] 下一轮在 {wait / 3600:.1f} 小时后")
        await asyncio.sleep(wait)
        try:
            await sample_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[采样] 本轮异常退出，明天再试: {e}")


def start() -> None:
    """启动每日采样任务（幂等，须在事件循环里调用）"""
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop(), name="MCWatcher-StatsSample")
    logger.info(f"[采样] 每日统计采样已启动，运行时刻 {SAMPLE_AT[0]:02d}:{SAMPLE_AT[1]:02d}")


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
