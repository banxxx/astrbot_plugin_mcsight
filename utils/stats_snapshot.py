"""玩家统计快照存储（插件本地 sqlite）

每日定时采样 + /查询 各落一份当日累计快照；读的时候按日历天挑基线，
所以时间轴由日历驱动，与「有没有人查询」无关。数据只在本插件目录。
"""

import json
import os
import sqlite3
import threading
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger

_DB_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 '..', 'data', 'stats_snapshots.sqlite3'))

RETENTION_DAYS = 30          # 超出即清理，控制体积


_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _open() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    _conn = sqlite3.connect(_DB_PATH, timeout=2.0, check_same_thread=False)
    _conn.execute(
        "CREATE TABLE IF NOT EXISTS stats_snapshot ("
        "  player TEXT NOT NULL,"
        "  server TEXT NOT NULL,"
        "  day TEXT NOT NULL,"
        "  captured_at REAL NOT NULL,"
        "  stats TEXT NOT NULL,"
        "  PRIMARY KEY (player, server, day))"
    )
    _conn.commit()
    return _conn


def _key(player: str, server: str) -> Tuple[str, str]:
    # 玩家名/服务器名大小写不同视为同一个，避免改名前后基线断掉
    return str(player).strip().lower(), str(server).strip().lower()


def _day_str(d: date) -> str:
    return d.strftime('%Y-%m-%d')


def _numeric_only(stats: Dict[str, Any]) -> Dict[str, float]:
    out = {}
    for k, v in (stats or {}).items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        out[str(k)] = float(v)
    return out


def load_snapshots(player: str, server: str,
                   keep_days: int = RETENTION_DAYS
                   ) -> List[Tuple[str, Dict[str, float]]]:
    """返回 (day, 当日累计值) 列表，按天升序，不含今天。失败返回空列表。

    挑哪一份当基线是出图侧的口径判断，这里只按日历把历史区间交出去。
    """
    try:
        p, s = _key(player, server)
        today = date.today()
        sql = ("SELECT day, stats FROM stats_snapshot"
               " WHERE player=? AND server=? AND day<? AND day>=?"
               " ORDER BY day ASC")
        with _lock:
            rows = _open().execute(
                sql, (p, s, _day_str(today),
                      _day_str(today - timedelta(days=keep_days)))).fetchall()
        out = []
        for day, raw in rows:
            try:
                stats = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if isinstance(stats, dict) and stats:
                out.append((day, stats))
        return out
    except Exception as e:
        logger.warning(f"读取统计快照失败: {e}")
        return []


def list_sampled(keep_days: int = RETENTION_DAYS) -> List[Tuple[str, str]]:
    """最近仍有快照的 (player, server)，即每日采样的名单。失败返回空列表。

    名单来自快照表本身：被查询过的人才会被继续采样，30 天保留期一到自动退出。
    名字是当初查询用的那个大小写，模组按名找 UUID 不区分大小写，可以直接回传。
    """
    try:
        since = _day_str(date.today() - timedelta(days=keep_days))
        with _lock:
            rows = _open().execute(
                "SELECT DISTINCT player, server FROM stats_snapshot"
                " WHERE day>=? ORDER BY player, server", (since,)).fetchall()
        return [(r[0], r[1]) for r in rows]
    except Exception as e:
        logger.warning(f"读取采样名单失败: {e}")
        return []


def save_snapshot(player: str, server: str, stats: Dict[str, Any]) -> None:
    """写入（覆盖）今天的快照，并清理超期数据。失败不抛异常。"""
    try:
        values = _numeric_only(stats)
        if not values:
            return
        p, s = _key(player, server)
        today = date.today()
        with _lock:
            conn = _open()
            conn.execute(
                "INSERT OR REPLACE INTO stats_snapshot"
                " (player, server, day, captured_at, stats) VALUES (?,?,?,?,?)",
                (p, s, _day_str(today), time.time(),
                 json.dumps(values)))
            conn.execute("DELETE FROM stats_snapshot WHERE day<?",
                         (_day_str(today - timedelta(days=RETENTION_DAYS)),))
            conn.commit()
    except Exception as e:
        logger.warning(f"写入统计快照失败: {e}")
