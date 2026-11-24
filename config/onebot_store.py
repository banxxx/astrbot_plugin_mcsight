"""OneBot 群配置的 SQLite 存储层。

一个 OneBot 实例使用一个 ``onebot/config.sqlite3``：
- ``groups`` / ``servers`` 保存群与服务器配置；
- ``server_tokens`` 单独保存命令 Token，不进入普通查询和可编辑备份；
- ``meta.revision`` 用于导出/导入时的并发校验。

本模块只负责数据库与事务，不解析旧 JSON，也不依赖 AstrBot；
旧文件迁移由 ``config.server_config`` 通过 ``bootstrap`` 回调提供。
"""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock
import time
from uuid import uuid4

DB_FILE_NAME = "config.sqlite3"
DB_SCHEMA_VERSION = 1
DB_TIMEOUT = 5.0
BACKUP_FORMAT = "mcsight.onebot.backup"
BACKUP_FORMAT_VERSION = 1
LEGACY_DIR_NAME = "legacy"


class OneBotError(ValueError):
    """安全、可显示的错误；不包含 SQL、绝对路径或底层异常。"""


def _text(value, field, limit=80):
    if (not isinstance(value, str) or len(value) > limit
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise OneBotError(f"OneBot 配置中的 {field} 无效。")
    return value


def _scope(kind, scope_id):
    if kind not in ("group", "private"):
        raise OneBotError("OneBot 群配置类型无效。")
    return kind, _text(str(scope_id).strip(), "群标识", 64)


class OneBotStore:
    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS groups (
            scope_kind TEXT NOT NULL CHECK (scope_kind IN ('group', 'private')),
            scope_id TEXT NOT NULL,
            enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
            alias TEXT NOT NULL,
            policy_json TEXT NOT NULL,
            nickname_ignore_json TEXT NOT NULL,
            PRIMARY KEY (scope_kind, scope_id)
        );
        CREATE TABLE IF NOT EXISTS servers (
            server_id TEXT PRIMARY KEY,
            scope_kind TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            name TEXT NOT NULL,
            host TEXT NOT NULL,
            api_port INTEGER,
            hidden INTEGER NOT NULL CHECK (hidden IN (0, 1)),
            remark TEXT NOT NULL,
            remark_src TEXT NOT NULL CHECK (remark_src IN ('manual', 'motd')),
            remark_motd TEXT NOT NULL,
            remark_motd_at INTEGER NOT NULL,
            remark_motd_runs_json TEXT,
            position INTEGER NOT NULL,
            FOREIGN KEY (scope_kind, scope_id)
                REFERENCES groups(scope_kind, scope_id) ON DELETE CASCADE,
            UNIQUE (scope_kind, scope_id, name)
        );
        CREATE INDEX IF NOT EXISTS servers_scope_position
            ON servers(scope_kind, scope_id, position);
        CREATE TABLE IF NOT EXISTS server_tokens (
            server_id TEXT PRIMARY KEY
                REFERENCES servers(server_id) ON DELETE CASCADE,
            token TEXT NOT NULL
        );
    """

    def __init__(self, root, *, clock=time.time):
        self.root = Path(root)
        self.clock = clock
        self.lock = RLock()
        self._conn = None
        self._root_key = None

    # ---------- 生命周期 ----------

    @property
    def db_path(self):
        return self.root / DB_FILE_NAME

    def close(self):
        with self.lock:
            self._close_connection()

    def _close_connection(self):
        conn = self._conn
        self._conn = None
        self._root_key = None
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def __del__(self):
        try:
            self._close_connection()
        except Exception:
            pass

    def _legacy_archive_present(self):
        base = self.root / LEGACY_DIR_NAME
        if not base.is_dir():
            return False
        try:
            for _root, _dirs, files in os.walk(base):
                if files:
                    return True
        except OSError:
            return False
        return False

    def ensure_ready(self, bootstrap=None, *, allow_legacy_archive=False):
        """确保数据库已打开；缺失时先迁移再原子替换。

        bootstrap 返回 ``(documents, sources)``；sources 只用于调用方留档，
        本层不移动、不删除任何旧文件。``allow_legacy_archive`` 只供显式导入
        备份时使用：正式库缺失但留档仍在时，允许先建空库再由导入整份恢复。
        """
        with self.lock:
            if self._conn is not None:
                return False, []
            path = self.db_path
            created = False
            sources = []
            if not path.is_file():
                documents, sources = bootstrap() if bootstrap is not None else ([], [])
                if not documents and self._legacy_archive_present() and not allow_legacy_archive:
                    raise OneBotError(
                        "OneBot 配置数据库缺失，但发现 legacy/ 历史留档；"
                        "为防止静默丢失，已暂停使用。请先导入配置备份，或确认留档不再需要后移走 legacy/。")
                self._create_db(documents)
                created = True
            try:
                self._connect()
            except OneBotError:
                raise
            except (OSError, sqlite3.Error):
                raise OneBotError(
                    "OneBot 配置缺损或无法读取；请恢复 config.sqlite3 后重试。") from None
            return created, sources

    def _connect(self):
        path = self.db_path
        if path.is_file() and self._conn is not None and self._root_key == str(self.root):
            return self._conn
        self._close_connection()
        conn = None
        try:
            conn = sqlite3.connect(str(path), timeout=DB_TIMEOUT,
                                   isolation_level=None, check_same_thread=False)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA trusted_schema = OFF")
            conn.execute("PRAGMA journal_mode = WAL")
            self._check_db(conn)
            self._conn = conn
            self._root_key = str(self.root)
            return conn
        except OneBotError:
            if conn is not None:
                conn.close()
            raise
        except (OSError, sqlite3.Error):
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            raise OneBotError(
                "OneBot 配置缺损或无法读取；请恢复 config.sqlite3 后重试。") from None

    def _check_db(self, conn):
        try:
            rows = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            revision = int(rows["revision"])
        except (sqlite3.Error, KeyError, TypeError, ValueError):
            raise OneBotError(
                "OneBot 配置缺损或无法读取；请恢复 config.sqlite3 后重试。") from None
        if rows.get("schema_version") != str(DB_SCHEMA_VERSION) or revision < 0:
            raise OneBotError("OneBot 配置数据库版本不匹配，已暂停使用。")

    def _create_db(self, documents):
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise OneBotError("无法创建 OneBot 配置目录，请检查插件数据目录权限。") from None
        temporary = self.root / (".config-" + uuid4().hex + ".tmp")
        conn = None
        try:
            conn = sqlite3.connect(str(temporary), timeout=DB_TIMEOUT,
                                   isolation_level=None)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA trusted_schema = OFF")
            conn.executescript(self._SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)",
                         (str(DB_SCHEMA_VERSION),))
            conn.execute("INSERT INTO meta(key, value) VALUES('revision', '0')")
            seen = set()
            for document in documents:
                doc = deepcopy(document)
                key = _scope(doc.get("scope_kind"), doc.get("scope_id"))
                if key in seen:
                    raise OneBotError("OneBot 旧配置中存在重复的群标识。")
                seen.add(key)
                self._write_group(conn, doc, bump=False)
            conn.execute("COMMIT")
            conn.close()
            conn = None
            os.replace(str(temporary), str(self.db_path))
            for suffix in ("-wal", "-shm", "-journal"):
                try:
                    Path(str(temporary) + suffix).unlink(missing_ok=True)
                except OSError:
                    pass
        except OneBotError:
            raise
        except (OSError, sqlite3.Error):
            raise OneBotError(
                "OneBot 旧配置迁移失败；未创建新数据库，原文件保持不动。") from None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            for suffix in ("", "-wal", "-shm", "-journal"):
                try:
                    Path(str(temporary) + suffix).unlink(missing_ok=True)
                except OSError:
                    pass

    # ---------- 事务与读写 ----------

    def _rollback(self, conn):
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            self._close_connection()

    @contextmanager
    def _transaction(self, *, write):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.execute("COMMIT")
        except OneBotError:
            self._rollback(conn)
            raise
        except sqlite3.Error:
            self._rollback(conn)
            raise OneBotError(
                "OneBot 配置保存失败，请刷新确认；未切换到旧平台数据。") from None
        except BaseException:
            self._rollback(conn)
            raise

    @staticmethod
    def _empty_document(kind, scope_id):
        return {
            "scope_kind": kind,
            "scope_id": scope_id,
            "enabled": True,
            "alias": "",
            "policy": {},
            "nickname_ignore": [],
            "servers": [],
        }

    @staticmethod
    def _json_load(raw, field):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            raise OneBotError(f"OneBot 配置中的 {field} 已损坏。") from None

    def _read_group(self, conn, kind, scope_id, *, include_tokens=True):
        row = conn.execute(
            "SELECT enabled, alias, policy_json, nickname_ignore_json "
            "FROM groups WHERE scope_kind = ? AND scope_id = ?",
            (kind, scope_id)).fetchone()
        if row is None:
            return None
        policy = self._json_load(row[2], "群规则")
        ignore = self._json_load(row[3], "忽略名单")
        if not isinstance(policy, dict) or not isinstance(ignore, list):
            raise OneBotError("OneBot 配置中的群规则或忽略名单已损坏。")
        servers = []
        token_rows = {}
        if include_tokens:
            token_rows = dict(conn.execute(
                "SELECT t.server_id, t.token FROM server_tokens t "
                "JOIN servers s ON s.server_id = t.server_id "
                "WHERE s.scope_kind = ? AND s.scope_id = ?",
                (kind, scope_id)))
        rows = conn.execute(
            "SELECT server_id, name, host, api_port, hidden, remark, remark_src, "
            "remark_motd, remark_motd_at, remark_motd_runs_json "
            "FROM servers WHERE scope_kind = ? AND scope_id = ? ORDER BY position",
            (kind, scope_id)).fetchall()
        for row_s in rows:
            entry = {
                "server_id": row_s[0],
                "name": row_s[1],
                "host": row_s[2],
                "api_port": row_s[3],
                "hidden": bool(row_s[4]),
                "remark": row_s[5],
                "remark_src": row_s[6],
                "remark_motd": row_s[7],
                "remark_motd_at": row_s[8],
            }
            if row_s[9] is not None:
                entry["remark_motd_runs"] = self._json_load(row_s[9], "MOTD 颜色")
            token = token_rows.get(row_s[0]) if include_tokens else None
            if token:
                entry["console_token"] = token
            servers.append(entry)
        return {
            "scope_kind": kind,
            "scope_id": scope_id,
            "enabled": bool(row[0]),
            "alias": row[1],
            "policy": policy,
            "nickname_ignore": ignore,
            "servers": servers,
        }

    def _write_group(self, conn, document, *, bump=True):
        kind, scope_id = _scope(document.get("scope_kind"), document.get("scope_id"))
        enabled = document.get("enabled", True)
        alias = document.get("alias", "")
        policy = document.get("policy", {})
        ignore = document.get("nickname_ignore", [])
        servers = document.get("servers", [])
        if type(enabled) is not bool:
            raise OneBotError("OneBot 群启用状态无效。")
        if not isinstance(alias, str) or not isinstance(policy, dict) or not isinstance(ignore, list):
            raise OneBotError("OneBot 群配置格式无效。")
        if not isinstance(servers, list):
            raise OneBotError("OneBot 服务器列表格式无效。")
        conn.execute(
            "INSERT INTO groups(scope_kind, scope_id, enabled, alias, policy_json, "
            "nickname_ignore_json) VALUES(?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(scope_kind, scope_id) DO UPDATE SET "
            "enabled = excluded.enabled, alias = excluded.alias, "
            "policy_json = excluded.policy_json, "
            "nickname_ignore_json = excluded.nickname_ignore_json",
            (kind, scope_id, int(enabled), alias,
             json.dumps(policy, ensure_ascii=False, separators=(",", ":")),
             json.dumps(ignore, ensure_ascii=False, separators=(",", ":"))))
        conn.execute("DELETE FROM servers WHERE scope_kind = ? AND scope_id = ?",
                     (kind, scope_id))
        names = set()
        for position, raw in enumerate(servers):
            if not isinstance(raw, dict):
                raise OneBotError("OneBot 服务器条目格式无效。")
            name = raw.get("name")
            host = raw.get("host")
            if not isinstance(name, str) or not name or not isinstance(host, str) or not host:
                raise OneBotError("OneBot 服务器名称或地址为空。")
            if name in names:
                raise OneBotError("OneBot 同一个群内存在重复的服务器名称。")
            names.add(name)
            server_id = raw.get("server_id")
            if not server_id:
                server_id = str(uuid4())
            if not isinstance(server_id, str) or len(server_id) > 64:
                raise OneBotError("OneBot 服务器标识无效。")
            api_port = raw.get("api_port")
            if api_port is not None and (type(api_port) is not int
                                         or not 1 <= api_port <= 65535):
                raise OneBotError("OneBot 服务器模组端口无效。")
            hidden = bool(raw.get("hidden", False))
            remark = raw.get("remark", "")
            remark_src = raw.get("remark_src", "manual")
            remark_motd = raw.get("remark_motd", "")
            remark_motd_at = raw.get("remark_motd_at", 0)
            runs = raw.get("remark_motd_runs")
            if (not isinstance(remark, str) or not isinstance(remark_motd, str)
                    or remark_src not in ("manual", "motd")
                    or type(remark_motd_at) not in (int, float)):
                raise OneBotError("OneBot 服务器备注格式无效。")
            conn.execute(
                "INSERT INTO servers(server_id, scope_kind, scope_id, name, host, "
                "api_port, hidden, remark, remark_src, remark_motd, remark_motd_at, "
                "remark_motd_runs_json, position) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (server_id, kind, scope_id, name, host, api_port, int(hidden),
                 remark, remark_src, remark_motd, int(remark_motd_at),
                 None if runs is None else json.dumps(runs, ensure_ascii=False,
                                                      separators=(",", ":")),
                 position))
            token = raw.get("console_token")
            if token:
                if not isinstance(token, str):
                    raise OneBotError("OneBot 命令 Token 格式无效。")
                conn.execute(
                    "INSERT INTO server_tokens(server_id, token) VALUES(?, ?)",
                    (server_id, token))
        if bump:
            conn.execute("UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
                         "WHERE key = 'revision'")

    def _revision(self, conn):
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = 'revision'").fetchone()
            value = int(row[0])
        except (sqlite3.Error, TypeError, ValueError, IndexError):
            raise OneBotError("OneBot 配置缺损或无法读取。") from None
        if value < 0:
            raise OneBotError("OneBot 配置缺损或无法读取。")
        return value

    # ---------- 公共读接口 ----------

    def list_groups(self, kind="group"):
        kind, _ = _scope(kind, "0")
        with self.lock, self._transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT scope_id FROM groups WHERE scope_kind = ? ORDER BY scope_id",
                (kind,)).fetchall()
        return [row[0] for row in rows]

    def list_group_summaries(self, kind="group"):
        kind, _ = _scope(kind, "0")
        with self.lock, self._transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT g.scope_id, g.alias, "
                "(SELECT COUNT(*) FROM servers s WHERE s.scope_kind = g.scope_kind "
                " AND s.scope_id = g.scope_id), "
                "(SELECT COUNT(*) FROM servers s WHERE s.scope_kind = g.scope_kind "
                " AND s.scope_id = g.scope_id AND s.api_port IS NOT NULL), "
                "(SELECT COUNT(*) FROM servers s JOIN server_tokens t "
                " ON t.server_id = s.server_id WHERE s.scope_kind = g.scope_kind "
                " AND s.scope_id = g.scope_id) "
                "FROM groups g WHERE g.scope_kind = ? ORDER BY g.scope_id",
                (kind,)).fetchall()
        return [
            {"gid": row[0], "alias": row[1], "server_count": row[2],
             "mod_count": row[3], "token_count": row[4]}
            for row in rows
        ]

    def get_group(self, kind, scope_id, *, include_tokens=True):
        kind, scope_id = _scope(kind, scope_id)
        with self.lock, self._transaction(write=False) as conn:
            return self._read_group(conn, kind, scope_id, include_tokens=include_tokens)

    def group_exists(self, kind, scope_id):
        kind, scope_id = _scope(kind, scope_id)
        with self.lock, self._transaction(write=False) as conn:
            row = conn.execute(
                "SELECT 1 FROM groups WHERE scope_kind = ? AND scope_id = ?",
                (kind, scope_id)).fetchone()
        return row is not None

    def all_enabled_server_endpoints(self):
        with self.lock, self._transaction(write=False) as conn:
            rows = conn.execute(
                "SELECT s.name, s.host, s.api_port FROM servers s "
                "JOIN groups g ON g.scope_kind = s.scope_kind AND g.scope_id = s.scope_id "
                "WHERE g.enabled = 1 AND s.hidden = 0 AND s.api_port IS NOT NULL "
                "ORDER BY g.scope_kind, g.scope_id, s.position").fetchall()
        return [(row[0], row[1], int(row[2])) for row in rows]

    # ---------- 公共写接口 ----------

    def ensure_group(self, kind, scope_id, document=None):
        kind, scope_id = _scope(kind, scope_id)
        with self.lock:
            if self.group_exists(kind, scope_id):
                return False
            with self._transaction(write=True) as conn:
                doc = deepcopy(document) if document is not None else self._empty_document(kind, scope_id)
                doc["scope_kind"], doc["scope_id"] = kind, scope_id
                self._write_group(conn, doc)
            return True

    def save_group(self, document):
        kind, scope_id = _scope(document.get("scope_kind"), document.get("scope_id"))
        with self.lock, self._transaction(write=True) as conn:
            self._write_group(conn, document)
        return True

    def mutate_group(self, kind, scope_id, mutator):
        """在写事务内读取-修改-写回一个群；mutator 返回 ``(result, changed)``。"""
        kind, scope_id = _scope(kind, scope_id)
        with self.lock, self._transaction(write=True) as conn:
            doc = self._read_group(conn, kind, scope_id)
            if doc is None:
                doc = self._empty_document(kind, scope_id)
            doc = deepcopy(doc)
            result, changed = mutator(doc)
            if changed:
                self._write_group(conn, doc)
            return result

    def delete_group(self, kind, scope_id):
        kind, scope_id = _scope(kind, scope_id)
        with self.lock, self._transaction(write=True) as conn:
            cur = conn.execute(
                "DELETE FROM groups WHERE scope_kind = ? AND scope_id = ?",
                (kind, scope_id))
            deleted = cur.rowcount > 0
            if deleted:
                conn.execute("UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
                             "WHERE key = 'revision'")
        return deleted

    # ---------- 导出 / 导入 ----------

    def export_state(self):
        """导出可编辑备份；不含控制台 Token。"""
        with self.lock, self._transaction(write=False) as conn:
            revision = self._revision(conn)
            rows = conn.execute(
                "SELECT scope_kind, scope_id FROM groups ORDER BY scope_kind, scope_id").fetchall()
            groups = [self._read_group(conn, row[0], row[1], include_tokens=False)
                      for row in rows]
        return {
            "format": BACKUP_FORMAT,
            "format_version": BACKUP_FORMAT_VERSION,
            "exported_at": int(self.clock()),
            "schema_version": DB_SCHEMA_VERSION,
            "revision": revision,
            "groups": groups,
        }

    def import_state(self, document, expected_revision, validator=None):
        if type(expected_revision) is not int:
            raise OneBotError("配置修订号无效。")
        if not isinstance(document, dict):
            raise OneBotError("OneBot 配置备份格式无效。")
        if validator is not None:
            documents = validator(document)
        else:
            documents = document.get("groups")
        if not isinstance(documents, list):
            raise OneBotError("OneBot 配置备份格式无效。")
        with self.lock, self._transaction(write=True) as conn:
            current = self._revision(conn)
            if expected_revision != current:
                raise OneBotError("配置已被其它操作修改，请刷新后重试。")
            tokens = dict(conn.execute("SELECT server_id, token FROM server_tokens"))
            seen_groups = set()
            seen_servers = set()
            clean = []
            for raw in documents:
                if not isinstance(raw, dict):
                    raise OneBotError("OneBot 配置备份格式无效。")
                key = _scope(raw.get("scope_kind"), raw.get("scope_id"))
                if key in seen_groups:
                    raise OneBotError("OneBot 配置备份中的群标识重复。")
                seen_groups.add(key)
                doc = deepcopy(raw)
                for server in doc.get("servers", []):
                    server_id = server.get("server_id")
                    if server_id:
                        if server_id in seen_servers:
                            raise OneBotError("OneBot 配置备份中的服务器标识重复。")
                        seen_servers.add(server_id)
                        if server_id in tokens:
                            server["console_token"] = tokens[server_id]
                clean.append(doc)
            conn.execute("DELETE FROM groups")
            for doc in clean:
                self._write_group(conn, doc, bump=False)
            new_revision = max(current, int(document.get("revision", 0))) + 1
            conn.execute("UPDATE meta SET value = ? WHERE key = 'revision'",
                         (str(new_revision),))
        return {
            "groups": len(clean),
            "servers": sum(len(doc.get("servers", [])) for doc in clean),
            "revision": new_revision,
        }


_stores = {}
_stores_lock = RLock()


def get_store(root):
    """按数据根复用连接；切换根目录时关闭旧连接。"""
    key = os.path.abspath(str(root))
    with _stores_lock:
        store = _stores.get(key)
        if store is None:
            store = OneBotStore(key)
            _stores[key] = store
        return store


def close_store(root=None):
    with _stores_lock:
        if root is None:
            stores = list(_stores.values())
            _stores.clear()
        else:
            key = os.path.abspath(str(root))
            store = _stores.pop(key, None)
            stores = [store] if store is not None else []
    for store in stores:
        store.close()


def close_all():
    close_store()
