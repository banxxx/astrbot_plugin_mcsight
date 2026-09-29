"""玩家统计快照存储（插件本地 sqlite）

/查询 每次取到模组的累计统计后落一份当日快照；下次查询时用「窗口内最早的那份」
做基线，从而画出"近 N 天增量"。数据只在本插件目录，后续确认有用再整体迁中心库。
"""

import json
import os
import sqlite3
import threading
import time
from datetime import date, timedelta
from typing import Any, Dict, Optional, Tuple

from astrbot.api import logger

_DB_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 '..', 'data', 'stats_snapshots.sqlite3'))

MIN_BASELINE_AGE_DAYS = 1    # 当天的快照不能给自己当基线
MAX_BASELINE_AGE_DAYS = 14   # 太久远的基线已经没有参照意义
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


def load_baseline(player: str, server: str,
                  max_age_days: int = MAX_BASELINE_AGE_DAYS,
                  min_age_days: int = MIN_BASELINE_AGE_DAYS
                  ) -> Optional[Tuple[int, Dict[str, float]]]:
    """返回 (间隔天数, 基线累计值)；没有可用基线时返回 None。失败不抛异常。"""
    try:
        p, s = _key(player, server)
        today = date.today()
        sql = ("SELECT day, stats FROM stats_snapshot"
               " WHERE player=? AND server=? AND day<? AND day>=?"
               " ORDER BY day ASC LIMIT 1")
        with _lock:
            row = _open().execute(
                sql, (p, s, _day_str(today),
                      _day_str(today - timedelta(days=max_age_days)))).fetchone()
        if not row:
            return None
        stats = json.loads(row[1])
        if not isinstance(stats, dict) or not stats:
            return None
        age = (today - date.fromisoformat(row[0])).days
        if age < min_age_days:
            return None
        return age, stats
    except Exception as e:
        logger.warning(f"读取统计快照基线失败: {e}")
        return None


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
