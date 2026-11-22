"""Official statistics history; explicit paths, scoped keys and no import-time I/O."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
import time
from threading import RLock

from .official_core import OfficialError, PLAYER_RE, identifier, uuid_value


KEEP_DAYS = 30
MAX_DATABASE_BYTES = 64 * 1024 * 1024
MAX_PLAYERS = 1000


def numeric_stats(data):
    if not isinstance(data, dict) or len(data) > 256:
        raise OfficialError("模组统计字段格式无效。")
    values = {}
    for key, value in data.items():
        if not isinstance(key, str) or len(key) > 64:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        try:
            parsed = float(value)
        except (OverflowError, ValueError):
            continue
        if math.isfinite(parsed) and parsed >= 0:
            values[key] = parsed
    return values


class OfficialStats:
    def __init__(self, root, *, clock=time.time):
        self.root, self.clock = Path(root), clock
        self.lock = RLock()

    def today(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc).date()

    def _path(self, environment):
        return self.root / uuid_value(environment) / "stats.sqlite3"

    @contextmanager
    def _connection(self, environment):
        path = self._path(environment)
        connection = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file() and path.stat().st_size > MAX_DATABASE_BYTES:
                raise OfficialError("官方统计历史达到容量上限，请安排维护。")
            connection = sqlite3.connect(str(path), timeout=2)
            connection.execute("PRAGMA trusted_schema=OFF")
            existing = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if existing and existing != {"stats_meta", "daily_stats"}:
                raise OfficialError("官方统计历史来源或结构不匹配。")
            if not existing:
                with connection:
                    connection.execute("CREATE TABLE stats_meta (environment_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL)")
                    connection.execute("INSERT INTO stats_meta VALUES (?, 1)", (environment,))
                    connection.execute("CREATE TABLE daily_stats ("
                        "group_id TEXT COLLATE BINARY NOT NULL, server_id TEXT COLLATE BINARY NOT NULL,"
                        "player_key TEXT NOT NULL, player_name TEXT NOT NULL, day TEXT NOT NULL,"
                        "captured_at REAL NOT NULL, stats TEXT NOT NULL,"
                        "PRIMARY KEY(group_id, server_id, player_key, day))")
                    connection.execute("CREATE INDEX daily_stats_day ON daily_stats(day)")
            if connection.execute("SELECT environment_id, schema_version FROM stats_meta").fetchall() != [(environment, 1)]:
                raise OfficialError("官方统计历史不属于当前环境。")
            yield connection
        except OfficialError:
            raise
        except (sqlite3.Error, OSError, ValueError, OverflowError):
            raise OfficialError("官方统计历史暂不可用，本次仍可展示生涯累计。") from None
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _key(group, server, player):
        identifier(group)
        uuid_value(server)
        if not isinstance(player, str) or not PLAYER_RE.fullmatch(player):
            raise OfficialError("统计玩家名无效。")
        return group, server, player.lower()

    def record(self, environment, group, server, player, data):
        """Load previous days before saving today's latest cumulative values."""
        key = self._key(group, server, player)
        values = numeric_stats(data)
        today = self.today()
        floor = (today - timedelta(days=KEEP_DAYS)).isoformat()
        with self.lock, self._connection(environment) as connection:
            rows = connection.execute("SELECT day, stats FROM daily_stats WHERE group_id=? AND server_id=?"
                " AND player_key=? AND day<? AND day>=? ORDER BY day", (*key, today.isoformat(), floor)).fetchall()
            snapshots = []
            for day, raw in rows:
                previous = json.loads(raw)
                if not isinstance(previous, dict):
                    raise OfficialError("官方统计快照格式无效，不使用旧平台数据兜底。")
                snapshots.append((day, numeric_stats(previous)))
            if values:
                known = connection.execute("SELECT 1 FROM daily_stats WHERE group_id=? AND server_id=?"
                    " AND player_key=? LIMIT 1", key).fetchone()
                if known is None:
                    count = connection.execute("SELECT COUNT(*) FROM (SELECT DISTINCT group_id,server_id,player_key"
                        " FROM daily_stats WHERE day>=?)", (floor,)).fetchone()[0]
                    if count >= MAX_PLAYERS:
                        raise OfficialError("官方统计采样名单已达上限，本次仅展示生涯累计。")
                with connection:
                    connection.execute("INSERT INTO daily_stats VALUES (?,?,?,?,?,?,?)"
                        " ON CONFLICT(group_id,server_id,player_key,day) DO UPDATE SET"
                        " player_name=excluded.player_name,captured_at=excluded.captured_at,stats=excluded.stats",
                        (*key, player, today.isoformat(), self.clock(), json.dumps(values, allow_nan=False)))
                    connection.execute("DELETE FROM daily_stats WHERE day<?", (floor,))
            return snapshots

    def roster(self, environment, *, limit=300):
        if type(limit) is not int or not 1 <= limit <= 300:
            raise OfficialError("统计采样数量无效。")
        floor = (self.today() - timedelta(days=KEEP_DAYS)).isoformat()
        with self.lock, self._connection(environment) as connection:
            # The game key itself is accepted case-insensitively by the current
            # Java player lookup and never serves as a filesystem path.
            rows = connection.execute("SELECT group_id,server_id,player_key FROM daily_stats WHERE day>=?"
                " GROUP BY group_id,server_id,player_key ORDER BY MAX(captured_at) DESC,group_id,server_id,player_key LIMIT ?",
                (floor, limit)).fetchall()
            for group, server, player in rows:
                self._key(group, server, player)
            return rows
