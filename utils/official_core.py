"""官方环境的纯逻辑与独立存储。无框架、网络或导入时文件访问。"""
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import tempfile
from threading import RLock
import time
import unicodedata
from uuid import UUID, uuid4

from .group_policy import default_policy


MAX_BYTES = 8 * 1024 * 1024
# 旧版 state.json 的格式版本。v1 的群 policy 可能缺少后加的字段，
# 迁移时只补缺失字段并升到 v2；已有值原样保留。
STATE_SCHEMA_VERSION = 2
# 后台导出的可编辑备份格式；与旧 state.json 分开命名，避免把备份文件
# 误当成数据库或旧版运行文件。
BACKUP_FORMAT = "mcsight.qq_official.backup"
BACKUP_FORMAT_VERSION = 1
# 每环境 SQLite 配置库的格式版本；与旧 state.json 的 STATE_SCHEMA_VERSION 无关。
DB_SCHEMA_VERSION = 1
DB_FILE_NAME = "config.sqlite3"
DB_TIMEOUT = 5.0
LEGACY_DIR_NAME = "legacy"
# 旧 JSON 全量读写受内存和 8 MB 文件上限限制；SQLite 改为按需查询，
# 环境级群数上限提高到 10,000，仍保留上限避免异常数据无限增长。
MAX_GROUPS = 10000
MAX_CANDIDATES = 1000
MAX_SERVERS = 20
GROUP_MEMBER_ROLES = ("administrators", "blacklist", "nickname_ignore")
CANDIDATE_TTL = 86400
REGISTER_GAP = 30
SETUP_TTL = 300
SETUP_KEEP = 3600
MAX_SETUP_CODES = 10
# 官方群内按 @成员 维护的补充名单；只存本环境目录，不写 OneBot 共用的公共名单。
ACCESS_LIST_KEYS = ("super_admins", "whitelist_admins", "blacklist")
MAX_ACCESS_ENTRIES = 200
ACCESS_FILE_LIMIT = 64 * 1024
SETUP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SETUP_CODE_RE = re.compile(r"^[A-HJ-NP-Z2-9]{6}$")
SETUP_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")


class OfficialError(ValueError):
    """安全、可显示的错误，不包含底层异常或真实记录。"""


def identifier(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 64 or value != value.strip()
            or any(ord(c) < 33 or ord(c) == 127 or 0xd800 <= ord(c) <= 0xdfff for c in value)):
        raise OfficialError("官方身份字段无效。")
    return value


def uuid_value(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise OfficialError("内部对象标识无效。") from None
    return value


def text(value, limit=80, *, empty=True):
    if (not isinstance(value, str) or len(value) > limit or (not empty and not value.strip())
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise OfficialError("文本为空、过长或包含控制字符。")
    return value.strip()


def normalize_motd_runs(value):
    """规整 MOTD 彩色分段为 [[text, #hex|None], ...]；空/非法返回 []。

    只信任长度有限的、纯文本 + 颜色 hex 的结构，任何怪形状都当没颜色。
    """
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:100]:
        if (not isinstance(item, (list, tuple)) or len(item) != 2
                or not isinstance(item[0], str) or not item[0]
                or any(ord(c) < 32 for c in item[0])):
            continue
        text_seg = item[0][:80]
        color = item[1]
        if color is not None and (not isinstance(color, str) or not re.match(r"^#[0-9a-fA-F]{6}$", color)):
            color = None
        out.append([text_seg, color])
    return out


def _display_width(value):
    """以显示宽度计数：全角/中日韩字符算 2，其余算 1。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in value)


def alias_text(value):
    """群别名最多 8 个中文字符或 16 个英文字符；空串表示不设置别名。"""
    value = text(value, 40, empty=True)
    if _display_width(value) > 16:
        raise OfficialError("群别名最多 8 个中文字符（或 16 个英文字符）。")
    return value


def display_group_text(value):
    """QQ 群号只用于展示，但必须是纯数字。"""
    value = text(value, 40)
    if value and not (value.isascii() and value.isdigit()):
        raise OfficialError("QQ 群号只能填写数字。")
    return value


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _setup_hash(environment, code):
    """验证码只存哈希；环境前缀避免不同官方环境之间复用同一份摘要。"""
    return hashlib.sha256(f"mcsight-setup:{environment}:{code}".encode("ascii")).hexdigest()


@dataclass(frozen=True)
class OfficialScope:
    environment_id: str
    instance_id: str
    group_id: str
    user_id: str
    message_id: str
    nickname: str = ""
    role: str = "member"
    mentions: tuple = ()
    mention_nicknames: tuple = ()

    @property
    def key(self):
        return (self.environment_id, self.instance_id, self.group_id, self.user_id)


def event_scope(event, selection):
    """只在实例门禁通过后调用。未知/频道/C2C/缺字段事件均拒绝。"""
    try:
        if (not isinstance(selection, dict) or selection.get("platform") != "qq_official"
                or event.get_platform_name() not in ("qq_official", "qq_official_webhook")
                or event.get_platform_id() != selection.get("instance_id")):
            return None
        message = event.message_obj
        raw = _get(message, "raw_message")
        data = _get(raw, "raw_data", raw)
        if not isinstance(data, dict):
            return None
        # 显式排除频道/私信；不能只看框架 GROUP_MESSAGE 标记。
        if data.get("channel_id") or data.get("guild_id") or data.get("openid"):
            return None
        group = identifier(data.get("group_openid"))
        author = data.get("author")
        if not isinstance(author, dict):
            return None
        user = identifier(author.get("member_openid"))
        message_id = data.get("id") or _get(message, "message_id")
        if (not isinstance(message_id, str) or not 1 <= len(message_id) <= 512
                or any(ord(c) < 33 or ord(c) == 127 for c in message_id)):
            return None
        if str(event.get_group_id()) != group or str(event.get_sender_id()) != user:
            return None
        raw_nickname = author.get("member_nickname")
        try:
            nickname = text(raw_nickname, 128)
            if any(0xd800 <= ord(c) <= 0xdfff for c in nickname):
                nickname = ""
        except OfficialError:
            nickname = ""  # Missing/malformed card must not disable unrelated group commands.
        # 仅识别明确文本角色；未知数字枚举不得猜测为管理员。
        role = author.get("member_role", "member")
        role = role if role in ("owner", "admin", "member") else "member"
        mentions=[]
        mention_nicknames={}
        raw_mentions=data.get("mentions",[])
        if isinstance(raw_mentions,list) and len(raw_mentions)<=20:
            for target in raw_mentions:
                if not isinstance(target,dict) or target.get("scope","single")!="single" or target.get("bot") is True or target.get("is_you") is True:
                    continue
                try: member=identifier(target.get("member_openid"))
                except OfficialError: continue
                if member not in mentions: mentions.append(member)
                try:
                    member_nickname=text(target.get("member_nickname"),128)
                    if any(0xd800 <= ord(c) <= 0xdfff for c in member_nickname):member_nickname=""
                except OfficialError:
                    member_nickname=""
                if member_nickname:mention_nicknames[member]=member_nickname
        return OfficialScope(uuid_value(selection["environment_id"]), selection["instance_id"],
                             group, user, message_id, nickname, role,tuple(mentions),tuple(mention_nicknames.items()))
    except (OfficialError, AttributeError, KeyError, TypeError, ValueError):
        return None


def atomic_json(path, data, limit=MAX_BYTES):
    raw = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > limit:
        raise OfficialError("官方配置容量已满，请安排维护。")
    path = Path(path)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".official-", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError:
        raise OfficialError("官方配置保存失败，请刷新确认；未切换到旧平台数据。") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def read_json(path, default, limit=MAX_BYTES):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("size")
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite")))
    except FileNotFoundError:
        return deepcopy(default)
    except (OSError, ValueError, RecursionError):
        raise OfficialError("官方配置缺损或无法读取；请恢复后重试，不会读取旧群配置。") from None


class OfficialStore:
    """每环境一个 SQLite 原子提交单元；群、服务器、名单与候选不跨库提交。

    旧版 ``state.json`` / ``access.json`` / ``setup_codes`` / ``remarks`` 在首次
    打开环境时一次性导入 ``config.sqlite3``，并移到 ``legacy/`` 留作核对。
    OpenID 只作为字段值，不参与路径拼接。
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS groups (
            config_id TEXT PRIMARY KEY,
            group_openid TEXT NOT NULL UNIQUE,
            alias TEXT NOT NULL,
            display_group TEXT NOT NULL,
            enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
            revision INTEGER NOT NULL CHECK (revision >= 1),
            policy_json TEXT NOT NULL,
            position INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS servers (
            server_id TEXT PRIMARY KEY,
            config_id TEXT NOT NULL REFERENCES groups(config_id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            host TEXT NOT NULL,
            game_port INTEGER NOT NULL,
            api_port INTEGER,
            hidden INTEGER NOT NULL CHECK (hidden IN (0, 1)),
            remark TEXT NOT NULL,
            position INTEGER NOT NULL,
            UNIQUE (config_id, name)
        );
        CREATE INDEX IF NOT EXISTS servers_config_position
            ON servers(config_id, position);
        CREATE TABLE IF NOT EXISTS group_members (
            config_id TEXT NOT NULL REFERENCES groups(config_id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('administrators', 'blacklist', 'nickname_ignore')),
            user_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            PRIMARY KEY (config_id, role, user_id)
        );
        CREATE INDEX IF NOT EXISTS group_members_config_role
            ON group_members(config_id, role, position);
        CREATE TABLE IF NOT EXISTS environment_admins (
            user_id TEXT PRIMARY KEY,
            position INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS candidates (
            candidate_id TEXT PRIMARY KEY,
            group_openid TEXT NOT NULL,
            member_openid TEXT NOT NULL,
            nickname TEXT NOT NULL,
            note TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            position INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS candidates_group_openid
            ON candidates(group_openid);
        CREATE INDEX IF NOT EXISTS candidates_expiry
            ON candidates(expires_at);
        CREATE TABLE IF NOT EXISTS environment_access (
            role TEXT NOT NULL CHECK (role IN ('super_admins', 'whitelist_admins', 'blacklist')),
            user_id TEXT NOT NULL,
            PRIMARY KEY (role, user_id)
        );
        CREATE TABLE IF NOT EXISTS setup_codes (
            setup_id TEXT PRIMARY KEY,
            alias TEXT NOT NULL,
            display_group TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            used_at REAL NOT NULL,
            config_id TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS setup_codes_expiry
            ON setup_codes(expires_at);
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            role TEXT NOT NULL,
            user_id TEXT NOT NULL,
            source_group TEXT NOT NULL,
            candidate_id TEXT,
            at REAL NOT NULL,
            source TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS server_remarks (
            server_id TEXT PRIMARY KEY REFERENCES servers(server_id) ON DELETE CASCADE,
            remark_src TEXT NOT NULL,
            remark_motd TEXT NOT NULL,
            remark_motd_at REAL NOT NULL,
            remark_motd_runs_json TEXT
        );
    """

    _REMARK_DEFAULT = {"remark_src": "manual", "remark_motd": "", "remark_motd_at": 0}

    def __init__(self, root, *, clock=time.time, token_provider=None, policy_defaults=None,
                 access_provider=None):
        self.root = Path(root)
        self.clock = clock
        self.lock = RLock()
        # 模组 API Token 与 OneBot 共用全局插件配置；注入取值函数，官方目录不再另存凭据。
        self.token_provider = token_provider or (lambda: "")
        # 可选策略默认值注入；生产环境不再读取全局官方开关，已有群不回溯覆盖。
        self.policy_defaults = policy_defaults
        # 公共权限名单与 OneBot 共用；官方事件用 openid 做精确匹配。
        self.access_provider = access_provider
        # 进程内复用一条 WAL 连接：每次点读重新 connect/PRAGMA 在 Windows
        # 上约 1.4 ms，复用后降到微秒级。所有公开方法先持有 self.lock，
        # 因此跨线程使用也由该锁串行化。
        self._db_conn = None
        self._db_environment = None
        # 仅显式导入备份时允许在 legacy/ 留档仍在的情况下重建空库；
        # 普通打开仍失败关闭，避免把“库丢了”误当成“新环境”。
        self._allow_empty_rebuild = False

    # ---------- 路径与数据库生命周期 ----------

    def _env_dir(self, environment):
        return self.root / uuid_value(environment)

    def _db_path(self, environment):
        return self._env_dir(environment) / DB_FILE_NAME

    def _legacy_state_path(self, environment):
        return self._env_dir(environment) / "state.json"

    def _legacy_access_path(self, environment):
        return self._env_dir(environment) / "access.json"

    def _legacy_setup_dir(self, environment):
        return self._env_dir(environment) / "setup_codes"

    def _legacy_remark_dir(self, environment):
        return self._env_dir(environment) / "remarks"

    def _console_token_path(self, environment, server_id):
        return (self._env_dir(environment) / "console_tokens"
                / (uuid_value(server_id) + ".json"))

    def close(self):
        """插件卸载/重载时释放 SQLite 文件句柄；之后仍可再次打开。"""
        with self.lock:
            self._close_connection()

    def _close_connection(self):
        conn = getattr(self, "_db_conn", None)
        self._db_conn = None
        self._db_environment = None
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def _drop_connection(self, conn):
        if getattr(self, "_db_conn", None) is conn:
            self._close_connection()
        else:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def __del__(self):
        try:
            self._close_connection()
        except Exception:
            pass

    def _conn(self, environment):
        environment = uuid_value(environment)
        path = self._db_path(environment)
        if path.is_file() and self._db_conn is not None and self._db_environment == environment:
            return self._db_conn
        self._close_connection()
        conn = None
        try:
            if not path.is_file():
                self._create_db(environment, path)
            conn = sqlite3.connect(str(path), timeout=DB_TIMEOUT, isolation_level=None,
                                   check_same_thread=False)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA trusted_schema = OFF")
            self._check_db(conn, environment)
            self._db_conn = conn
            self._db_environment = environment
            return conn
        except OfficialError:
            if conn is not None:
                self._drop_connection(conn)
            raise
        except (OSError, sqlite3.Error):
            if conn is not None:
                self._drop_connection(conn)
            raise OfficialError(
                "官方配置缺损或无法读取；请恢复后重试，不会读取旧群配置。") from None

    def _check_db(self, conn, environment):
        try:
            rows = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            revision = int(rows["revision"])
        except (sqlite3.Error, KeyError, TypeError, ValueError):
            raise OfficialError(
                "官方配置缺损或无法读取；请恢复后重试，不会读取旧群配置。") from None
        if (rows.get("schema_version") != str(DB_SCHEMA_VERSION)
                or rows.get("environment_id") != environment
                or revision < 0):
            raise OfficialError("官方配置数据库版本或环境不匹配，已暂停使用。")

    def _rollback(self, conn):
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            self._drop_connection(conn)

    @contextmanager
    def _transaction(self, environment, *, write):
        conn = self._conn(environment)
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.execute("COMMIT")
        except OfficialError:
            self._rollback(conn)
            raise
        except sqlite3.Error:
            self._rollback(conn)
            self._drop_connection(conn)
            raise OfficialError(
                "官方配置保存失败，请刷新确认；未切换到旧平台数据。") from None
        except BaseException:
            self._rollback(conn)
            raise

    def _create_db(self, environment, path):
        """先写临时库并完成旧数据导入，再原子替换为正式库。"""
        environment = uuid_value(environment)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise OfficialError("无法创建官方配置目录，请检查插件数据目录权限。") from None
        if path.is_file():
            return
        # 正式库缺失但 legacy/ 里还有历史配置时不能静默建空库：那会让后台
        # 看起来“恢复成功”，实际把所有官方群配置变成空白。要求用户先从备份
        # 恢复，或明确移走留档后再重建。
        if (not self._allow_empty_rebuild
                and self._legacy_archive_present(environment)):
            raise OfficialError(
                "官方配置数据库缺失，但发现 legacy/ 历史留档；为防止静默丢失，已暂停使用。"
                "请从备份恢复 config.sqlite3，或先在后台导入配置备份后再重建。")
        legacy_state = self._read_legacy_state(environment)
        legacy_access = self._read_legacy_access(environment)
        legacy_setups = self._read_legacy_setups(environment)
        legacy_remarks = self._read_legacy_remarks(environment)
        temporary = path.parent / (".config-" + uuid4().hex + ".tmp")
        conn = None
        try:
            conn = sqlite3.connect(str(temporary), timeout=DB_TIMEOUT, isolation_level=None)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA trusted_schema = OFF")
            conn.executescript(self._SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            revision = legacy_state["revision"] if legacy_state else 0
            conn.executemany("INSERT INTO meta(key, value) VALUES(?, ?)", (
                ("schema_version", str(DB_SCHEMA_VERSION)),
                ("environment_id", environment),
                ("revision", str(revision)),
            ))
            if legacy_state:
                self._insert_legacy_state(conn, legacy_state)
            if legacy_access:
                self._insert_legacy_access(conn, legacy_access)
            for entry in legacy_setups:
                self._insert_setup_row(conn, entry)
            self._insert_legacy_remarks(conn, legacy_remarks)
            conn.execute("COMMIT")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.close()
            conn = None
            os.replace(temporary, path)
        except OfficialError:
            raise
        except (OSError, sqlite3.Error):
            raise OfficialError(
                "官方配置迁移失败；未创建新数据库，旧配置保持不动。") from None
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
        self._archive_legacy(environment)

    def _legacy_archive_present(self, environment):
        """正式库缺失时，legacy/ 中是否还有需要保护的历史配置。"""
        archive = self._env_dir(environment) / LEGACY_DIR_NAME
        if (archive / "state.json").is_file() or (archive / "access.json").is_file():
            return True
        for name in ("setup_codes", "remarks"):
            directory = archive / name
            try:
                if directory.is_dir() and any(directory.iterdir()):
                    return True
            except OSError:
                return True
        return False

    def _archive_legacy(self, environment):
        """把成功导入的旧文件移到 legacy/；移动失败不阻断新库使用。"""
        directory = self._env_dir(environment)
        archive = directory / LEGACY_DIR_NAME
        try:
            archive.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        for name in ("state.json", "access.json", "setup_codes", "remarks"):
            source = directory / name
            if not source.exists():
                continue
            target = archive / name
            if target.exists():
                target = archive / (name + "." + str(int(time.time() * 1000)))
            try:
                shutil.move(str(source), str(target))
            except (OSError, shutil.Error):
                pass

    def _insert_legacy_state(self, conn, data):
        for position, group in enumerate(data["groups"]):
            self._insert_group_row(conn, group, position)
        for position, user in enumerate(data["environment_administrators"]):
            conn.execute("INSERT INTO environment_admins(user_id, position) VALUES(?, ?)",
                         (user, position))
        for position, candidate in enumerate(data["candidates"]):
            conn.execute(
                "INSERT INTO candidates(candidate_id, group_openid, member_openid, nickname, "
                "note, created_at, expires_at, position) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (candidate["candidate_id"], candidate["group_openid"],
                 candidate["member_openid"], candidate["nickname"], candidate["note"],
                 candidate["created_at"], candidate["expires_at"], position))
        for record in data["authorization_history"]:
            conn.execute(
                "INSERT INTO audit_log(action, role, user_id, source_group, candidate_id, at, source) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (record["action"], record["role"], record["user_id"], record["source_group"],
                 record["candidate_id"], record["at"], record["source"]))

    def _insert_group_row(self, conn, group, position):
        conn.execute(
            "INSERT INTO groups(config_id, group_openid, alias, display_group, enabled, "
            "revision, policy_json, position) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (group["config_id"], group["group_openid"], group["alias"],
             group["display_group"], 1 if group["enabled"] else 0, group["revision"],
             self._policy_json(group["policy"]), position))
        for role in GROUP_MEMBER_ROLES:
            for member_position, user in enumerate(group[role]):
                conn.execute(
                    "INSERT INTO group_members(config_id, role, user_id, position) "
                    "VALUES(?, ?, ?, ?)",
                    (group["config_id"], role, user, member_position))
        for server_position, server in enumerate(group["servers"]):
            conn.execute(
                "INSERT INTO servers(server_id, config_id, name, host, game_port, api_port, "
                "hidden, remark, position) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (server["server_id"], group["config_id"], server["name"], server["host"],
                 server["game_port"], server["api_port"], 1 if server["hidden"] else 0,
                 server["remark"], server_position))

    def _insert_legacy_access(self, conn, access):
        for role in ACCESS_LIST_KEYS:
            for user in access[role]:
                conn.execute("INSERT OR IGNORE INTO environment_access(role, user_id) "
                             "VALUES(?, ?)", (role, user))

    def _insert_setup_row(self, conn, entry):
        conn.execute(
            "INSERT OR REPLACE INTO setup_codes(setup_id, alias, display_group, code_hash, "
            "created_at, expires_at, used_at, config_id) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (entry["setup_id"], entry["alias"], entry["display_group"], entry["code_hash"],
             entry["created_at"], entry["expires_at"], entry["used_at"],
             entry["config_id"] or ""))

    def _insert_legacy_remarks(self, conn, remarks):
        known = {row[0] for row in conn.execute("SELECT server_id FROM servers")}
        for server_id, remark in remarks.items():
            if server_id not in known:
                continue
            runs = remark.get("remark_motd_runs")
            conn.execute(
                "INSERT OR REPLACE INTO server_remarks(server_id, remark_src, remark_motd, "
                "remark_motd_at, remark_motd_runs_json) VALUES(?, ?, ?, ?, ?)",
                (server_id, remark["remark_src"], remark["remark_motd"],
                 remark["remark_motd_at"],
                 json.dumps(runs, ensure_ascii=False, separators=(",", ":")) if runs else None))

    # ---------- 旧 JSON 读取与迁移 ----------

    def _read_legacy_state(self, environment):
        path = self._legacy_state_path(environment)
        if not path.is_file():
            return None
        data = read_json(path, None, limit=MAX_BYTES)
        if data is None:
            raise OfficialError(
                "官方配置缺损或无法读取；请恢复后重试，不会读取旧群配置。")
        data, _ = self._migrate_v1(environment, data)
        self._validate_state(environment, data)
        return data

    def _read_legacy_access(self, environment):
        path = self._legacy_access_path(environment)
        if not path.is_file():
            return None
        default = {"schema_version": 1, **{key: [] for key in ACCESS_LIST_KEYS}}
        data = read_json(path, default, limit=ACCESS_FILE_LIMIT)
        return self._validate_access_payload(data)

    def _read_legacy_setups(self, environment):
        directory = self._legacy_setup_dir(environment)
        if not directory.is_dir():
            return []
        entries = []
        for path in directory.glob("*.json"):
            data = self._load_setup(path)
            if data is not None:
                entries.append(data)
        return entries

    def _read_legacy_remarks(self, environment):
        directory = self._legacy_remark_dir(environment)
        if not directory.is_dir():
            return {}
        result = {}
        for path in directory.glob("*.json"):
            try:
                server_id = uuid_value(path.stem)
            except OfficialError:
                continue
            try:
                data = read_json(path, {}, limit=8192)
            except OfficialError:
                continue
            result[server_id] = self._remark_payload(data)
        return result

    def _migrate_v1(self, environment, data):
        """v1 -> v2：只补 policy 中缺失的字段，不覆盖用户已经设置的值。"""
        expected = {"schema_version", "environment_id", "revision", "groups", "candidates",
                    "environment_administrators", "authorization_history"}
        if (not isinstance(data, dict) or data.get("schema_version") != 1
                or set(data) != expected or data.get("environment_id") != environment):
            return data, False
        groups = data.get("groups")
        if not isinstance(groups, list):
            return data, False
        if any(not isinstance(group, dict) or not isinstance(group.get("policy"), dict)
               for group in groups):
            return data, False
        defaults = default_policy()
        for group in groups:
            for key, value in defaults.items():
                group["policy"].setdefault(key, value)
        data["schema_version"] = STATE_SCHEMA_VERSION
        return data, True

    def _validate_state(self, environment, data):
        try:
            if (not isinstance(data, dict) or set(data) != {"schema_version", "environment_id", "revision",
                    "groups", "candidates", "environment_administrators", "authorization_history"}
                    or type(data["schema_version"]) is not int or data["schema_version"] != STATE_SCHEMA_VERSION
                    or data["environment_id"] != environment
                    or type(data["revision"]) is not int or data["revision"] < 0
                    or not isinstance(data["groups"], list) or len(data["groups"]) > MAX_GROUPS
                    or not isinstance(data["candidates"], list) or len(data["candidates"]) > MAX_CANDIDATES
                    or not isinstance(data["environment_administrators"], list)
                    or len(data["environment_administrators"]) > 1000
                    or not isinstance(data["authorization_history"], list)
                    or len(data["authorization_history"]) > 10000):
                raise ValueError
            for user in data["environment_administrators"]:
                identifier(user)
            if len(data["environment_administrators"]) != len(set(data["environment_administrators"])):
                raise ValueError
            for record in data["authorization_history"]:
                if (not isinstance(record, dict)
                        or set(record) != {"action", "role", "user_id", "source_group", "candidate_id", "at", "source"}
                        or record["action"] not in ("grant", "revoke")
                        or record["role"] not in ("member", "group_admin", "environment_admin", "blacklist", "nickname_ignore")
                        or record["source"] != "authenticated_webui"
                        or type(record["at"]) not in (int, float)):
                    raise ValueError
                identifier(record["user_id"])
                identifier(record["source_group"])
                if record["candidate_id"] is not None:
                    uuid_value(record["candidate_id"])
            configs, group_ids = set(), set()
            for group in data["groups"]:
                uuid_value(group["config_id"])
                identifier(group["group_openid"])
                if group["config_id"] in configs or group["group_openid"] in group_ids:
                    raise ValueError
                configs.add(group["config_id"])
                group_ids.add(group["group_openid"])
                self._validate_group(group)
            candidate_ids = set()
            for candidate in data["candidates"]:
                uuid_value(candidate["candidate_id"])
                identifier(candidate["group_openid"])
                identifier(candidate["member_openid"])
                text(candidate["nickname"], 128)
                text(candidate["note"], 40)
                if (candidate["candidate_id"] in candidate_ids
                        or type(candidate["created_at"]) not in (int, float)
                        or type(candidate["expires_at"]) not in (int, float)):
                    raise ValueError
                candidate_ids.add(candidate["candidate_id"])
        except (KeyError, ValueError, TypeError, AttributeError):
            raise OfficialError(
                "官方配置结构无效；已暂停使用，请恢复有效记录。") from None

    @staticmethod
    def _validate_access_payload(data):
        expected = {"schema_version", *ACCESS_LIST_KEYS}
        if (not isinstance(data, dict) or data.get("schema_version") != 1
                or set(data) != expected):
            raise OfficialError("官方群名单配置缺损或格式不受支持。")
        result = {}
        for key in ACCESS_LIST_KEYS:
            value = data[key]
            if not isinstance(value, list) or len(value) > MAX_ACCESS_ENTRIES:
                raise OfficialError("官方群名单配置缺损或格式不受支持。")
            entries = set()
            for item in value:
                entries.add(identifier(item))
            result[key] = frozenset(entries)
        return result

    def _load_setup(self, path):
        try:
            data = read_json(path, {}, limit=4096)
        except OfficialError:
            return None
        try:
            uuid_value(data["config_id"]) if data["config_id"] else None
        except (OfficialError, KeyError, TypeError):
            return None
        if (not isinstance(data, dict)
                or set(data) != {"schema_version", "environment_id", "setup_id", "alias",
                                 "display_group", "code_hash", "created_at", "expires_at",
                                 "used_at", "config_id"}
                or type(data["schema_version"]) is not int or data["schema_version"] != 1
                or data["environment_id"] != path.parent.parent.name
                or data["setup_id"] != path.stem
                or not isinstance(data["alias"], str) or len(data["alias"]) > 40
                or any(ord(c) < 32 or ord(c) == 127 for c in data["alias"])
                or not isinstance(data["display_group"], str) or len(data["display_group"]) > 40
                or any(ord(c) < 32 or ord(c) == 127 for c in data["display_group"])
                or not isinstance(data["code_hash"], str)
                or (data["code_hash"] and not SETUP_HASH_RE.fullmatch(data["code_hash"]))
                or type(data["created_at"]) not in (int, float)
                or type(data["expires_at"]) not in (int, float)
                or type(data["used_at"]) not in (int, float)
                or data["used_at"] < 0 or data["expires_at"] < data["created_at"]
                or (data["used_at"] > 0 and not data["config_id"])
                or (data["used_at"] == 0 and (not data["code_hash"] or data["config_id"]))):
            return None
        return data

    # ---------- 读取 ----------

    @staticmethod
    def _policy_json(policy):
        try:
            return json.dumps(policy, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError):
            raise OfficialError("群策略字段无效。") from None

    @staticmethod
    def _policy_value(raw):
        try:
            return json.loads(raw, object_pairs_hook=_unique)
        except (TypeError, ValueError, RecursionError):
            raise OfficialError(
                "官方配置结构无效；已暂停使用，请恢复有效记录。") from None

    def _read_group_rows(self, conn, *, config_id=None, group_openid=None):
        sql = ("SELECT config_id, group_openid, alias, display_group, enabled, revision, "
               "policy_json FROM groups")
        params = ()
        if config_id is not None:
            sql += " WHERE config_id = ?"
            params = (config_id,)
        elif group_openid is not None:
            sql += " WHERE group_openid = ?"
            params = (group_openid,)
        sql += " ORDER BY position, config_id"
        return conn.execute(sql, params).fetchall()

    def _read_groups(self, conn, *, config_id=None, group_openid=None, full_validate=True):
        rows = self._read_group_rows(conn, config_id=config_id, group_openid=group_openid)
        if not rows:
            return []
        if len(rows) > MAX_GROUPS:
            raise OfficialError("官方群数量超过当前存储上限，请安排维护。")
        configs = [row[0] for row in rows]
        server_map = {key: [] for key in configs}
        member_map = {key: {role: [] for role in GROUP_MEMBER_ROLES} for key in configs}
        if len(configs) == 1:
            config_id_value = configs[0]
            server_rows = conn.execute(
                "SELECT server_id, config_id, name, host, game_port, api_port, hidden, remark "
                "FROM servers WHERE config_id = ? ORDER BY position, server_id",
                (config_id_value,)).fetchall()
            member_rows = conn.execute(
                "SELECT config_id, role, user_id FROM group_members WHERE config_id = ? "
                "ORDER BY role, position, user_id", (config_id_value,)).fetchall()
        else:
            server_rows = conn.execute(
                "SELECT server_id, config_id, name, host, game_port, api_port, hidden, remark "
                "FROM servers ORDER BY config_id, position, server_id").fetchall()
            member_rows = conn.execute(
                "SELECT config_id, role, user_id FROM group_members "
                "ORDER BY config_id, role, position, user_id").fetchall()
        for server_id, owner, name, host, game_port, api_port, hidden, remark in server_rows:
            if owner not in server_map:
                continue
            server_map[owner].append({
                "server_id": server_id, "name": name, "host": host,
                "game_port": game_port, "api_port": api_port,
                "hidden": bool(hidden), "remark": remark,
            })
        for owner, role, user_id in member_rows:
            if owner in member_map and role in member_map[owner]:
                member_map[owner][role].append(user_id)
        groups = []
        try:
            for config_id_value, group_openid_value, alias, display_group, enabled, revision, policy_raw in rows:
                group = {
                    "config_id": config_id_value,
                    "group_openid": group_openid_value,
                    "revision": revision,
                    "alias": alias,
                    "display_group": display_group,
                    "enabled": bool(enabled),
                    "servers": server_map[config_id_value],
                    "policy": self._policy_value(policy_raw),
                    "administrators": member_map[config_id_value]["administrators"],
                    "blacklist": member_map[config_id_value]["blacklist"],
                    "nickname_ignore": member_map[config_id_value]["nickname_ignore"],
                }
                if full_validate:
                    self._validate_group(group)
                else:
                    self._validate_group_shape(group)
                groups.append(group)
        except OfficialError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            raise OfficialError(
                "官方配置结构无效；已暂停使用，请恢复有效记录。") from None
        return groups

    def _read_candidates(self, conn, now):
        rows = conn.execute(
            "SELECT candidate_id, group_openid, member_openid, nickname, note, "
            "created_at, expires_at FROM candidates WHERE expires_at > ? "
            "ORDER BY position, candidate_id", (now,)).fetchall()
        if len(rows) > MAX_CANDIDATES:
            raise OfficialError("登记候选超过存储上限，请安排维护。")
        result = []
        try:
            for candidate_id, group_openid, member_openid, nickname, note, created_at, expires_at in rows:
                uuid_value(candidate_id)
                identifier(group_openid)
                identifier(member_openid)
                text(nickname, 128)
                text(note, 40)
                if type(created_at) not in (int, float) or type(expires_at) not in (int, float):
                    raise ValueError
                result.append({"candidate_id": candidate_id, "group_openid": group_openid,
                               "member_openid": member_openid, "nickname": nickname,
                               "note": note, "created_at": created_at,
                               "expires_at": expires_at})
        except OfficialError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            raise OfficialError(
                "官方配置结构无效；已暂停使用，请恢复有效记录。") from None
        return result

    def _read_admins(self, conn):
        rows = conn.execute(
            "SELECT user_id FROM environment_admins ORDER BY position, user_id").fetchall()
        if len(rows) > 1000:
            raise OfficialError("环境管理员数量超过存储上限，请安排维护。")
        result = []
        try:
            for (user,) in rows:
                result.append(identifier(user))
        except OfficialError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            raise OfficialError(
                "官方配置结构无效；已暂停使用，请恢复有效记录。") from None
        return result

    def _read_history(self, conn):
        rows = conn.execute(
            "SELECT action, role, user_id, source_group, candidate_id, at, source "
            "FROM audit_log ORDER BY id").fetchall()
        if len(rows) > 10000:
            raise OfficialError("授权记录超过存储上限，请安排维护。")
        result = []
        try:
            for action, role, user_id, source_group, candidate_id, at, source in rows:
                if (action not in ("grant", "revoke")
                        or role not in ("member", "group_admin", "environment_admin",
                                        "blacklist", "nickname_ignore")
                        or source != "authenticated_webui"
                        or type(at) not in (int, float)):
                    raise ValueError
                identifier(user_id)
                identifier(source_group)
                if candidate_id is not None:
                    uuid_value(candidate_id)
                result.append({"action": action, "role": role, "user_id": user_id,
                               "source_group": source_group, "candidate_id": candidate_id,
                               "at": at, "source": source})
        except OfficialError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            raise OfficialError(
                "官方配置结构无效；已暂停使用，请恢复有效记录。") from None
        return result

    def list_state(self, environment):
        environment = uuid_value(environment)
        with self.lock, self._transaction(environment, write=False) as conn:
            revision = self._env_revision(conn)
            # 列表页只做结构校验；点读和所有写入仍走完整校验，避免 2000 群
            # × 20 服时重复执行 UUID/主机正则而把页面读取拖到数百毫秒。
            groups = self._read_groups(conn, full_validate=False)
            candidates = self._read_candidates(conn, self.clock())
            admins = self._read_admins(conn)
            history = self._read_history(conn)
        return {"schema_version": STATE_SCHEMA_VERSION, "environment_id": environment,
                "revision": revision, "groups": groups, "candidates": candidates,
                "environment_administrators": admins,
                "authorization_history": history}

    def group(self, environment, *, group_id=None, config_id=None):
        environment = uuid_value(environment)
        if config_id is not None:
            uuid_value(config_id)
        if group_id is not None:
            identifier(group_id)
        if config_id is None and group_id is None:
            return None
        with self.lock, self._transaction(environment, write=False) as conn:
            groups = self._read_groups(conn, config_id=config_id, group_openid=group_id)
        return groups[0] if groups else None

    def environment_administrators(self, environment):
        """只读取环境管理员；官方群每条消息的权限检查不再加载全部群配置。"""
        environment = uuid_value(environment)
        with self.lock, self._transaction(environment, write=False) as conn:
            return self._read_admins(conn)

    def _read_remarks(self, conn, server_ids=None):
        """从一条已有事务读取备注；server_ids 已由调用方校验。"""
        if server_ids is None:
            rows = conn.execute(
                "SELECT server_id, remark_src, remark_motd, remark_motd_at, "
                "remark_motd_runs_json FROM server_remarks").fetchall()
        else:
            unique = list(dict.fromkeys(server_ids))
            if not unique:
                return {}
            rows = []
            for start in range(0, len(unique), 900):
                chunk = unique[start:start + 900]
                placeholders = ",".join("?" for _ in chunk)
                rows.extend(conn.execute(
                    "SELECT server_id, remark_src, remark_motd, remark_motd_at, "
                    "remark_motd_runs_json FROM server_remarks "
                    f"WHERE server_id IN ({placeholders})", chunk).fetchall())
        result = {}
        for server_id, remark_src, remark_motd, remark_motd_at, runs_json in rows:
            result[server_id] = self._remark_from_row(
                remark_src, remark_motd, remark_motd_at, runs_json)
        return result

    def server_remarks(self, environment, server_ids=None):
        """批量读取备注；返回 server_id -> 备注字典，缺省项不写入结果。"""
        environment = uuid_value(environment)
        if server_ids is not None:
            server_ids = [uuid_value(server_id) for server_id in server_ids]
            if not server_ids:
                return {}
        with self.lock, self._transaction(environment, write=False) as conn:
            return self._read_remarks(conn, server_ids)

    # ---------- 可编辑备份：导出 / 导入 ----------

    @staticmethod
    def _backup_remark(payload):
        base = {"remark_src", "remark_motd", "remark_motd_at"}
        if (not isinstance(payload, dict)
                or set(payload) - (base | {"remark_motd_runs"})
                or not base <= set(payload)
                or payload["remark_src"] not in ("manual", "motd")
                or not isinstance(payload["remark_motd"], str)
                or len(payload["remark_motd"]) > 80
                or any(ord(c) < 32 or ord(c) == 127 for c in payload["remark_motd"])
                or type(payload["remark_motd_at"]) not in (int, float)):
            raise OfficialError("官方服务器备注备份格式无效。")
        return OfficialStore._remark_payload(payload)

    def _validate_backup(self, environment, document):
        """校验后台备份；返回可写入的 state、补充名单和备注。"""
        keys = {"format", "format_version", "exported_at", "schema_version",
                "environment_id", "revision", "groups", "candidates",
                "environment_administrators", "environment_access", "server_remarks"}
        if (not isinstance(document, dict) or set(document) != keys
                or document.get("format") != BACKUP_FORMAT
                or type(document.get("format_version")) is not int
                or document["format_version"] != BACKUP_FORMAT_VERSION
                or document.get("environment_id") != environment
                or type(document.get("exported_at")) not in (int, float)):
            raise OfficialError("官方配置备份格式无效。")
        state = {key: document[key] for key in (
            "schema_version", "environment_id", "revision", "groups", "candidates",
            "environment_administrators")}
        state["authorization_history"] = []
        self._validate_state(environment, state)
        access_data = document["environment_access"]
        if not isinstance(access_data, dict) or set(access_data) != set(ACCESS_LIST_KEYS):
            raise OfficialError("官方群名单备份格式无效。")
        access = self._validate_access_payload({"schema_version": 1, **access_data})
        remarks_data = document["server_remarks"]
        if not isinstance(remarks_data, dict):
            raise OfficialError("官方服务器备注备份格式无效。")
        server_ids = set()
        for group in state["groups"]:
            for server in group["servers"]:
                if server["server_id"] in server_ids:
                    raise OfficialError("官方配置备份中的服务器标识重复。")
                server_ids.add(server["server_id"])
        remarks = {}
        for raw_id, payload in remarks_data.items():
            server_id = uuid_value(raw_id)
            remark = self._backup_remark(payload)
            # 备份里多出来的备注不阻断恢复；对应服务器不在本次备份中就不写入。
            if server_id in server_ids:
                remarks[server_id] = remark
        return state, access, remarks

    def export_state(self, environment):
        """导出可编辑的 JSON 备份；不包含验证码、审计明细和控制台 Token。"""
        environment = uuid_value(environment)
        with self.lock, self._transaction(environment, write=False) as conn:
            revision = self._env_revision(conn)
            groups = self._read_groups(conn)
            candidates = self._read_candidates(conn, self.clock())
            admins = self._read_admins(conn)
            access = self._read_environment_access(conn)
            remarks = self._read_remarks(conn)
        return {
            "format": BACKUP_FORMAT,
            "format_version": BACKUP_FORMAT_VERSION,
            "exported_at": int(self.clock()),
            "schema_version": STATE_SCHEMA_VERSION,
            "environment_id": environment,
            "revision": revision,
            "groups": groups,
            "candidates": candidates,
            "environment_administrators": admins,
            "environment_access": {key: sorted(access[key]) for key in ACCESS_LIST_KEYS},
            "server_remarks": remarks,
        }

    def import_state(self, environment, document, expected_revision):
        """用后台备份整份替换配置；校验通过后单事务提交并提升修订号。"""
        environment = uuid_value(environment)
        if type(expected_revision) is not int:
            raise OfficialError("配置修订号无效。")
        state, access, remarks = self._validate_backup(environment, deepcopy(document))
        now = self.clock()
        candidates = [item for item in state["candidates"] if item["expires_at"] > now]
        path = self._db_path(environment)
        with self.lock:
            created_db = not path.is_file()
            try:
                if created_db:
                    self._allow_empty_rebuild = True
                with self._transaction(environment, write=True) as conn:
                    current = self._env_revision(conn)
                    if expected_revision != current:
                        raise OfficialError("配置已被其它操作修改，请刷新后重试。")
                    previous_revisions = dict(conn.execute(
                        "SELECT config_id, revision FROM groups"))
                    old_server_ids = {row[0] for row in conn.execute(
                        "SELECT server_id FROM servers")}
                    conn.execute("DELETE FROM groups")
                    conn.execute("DELETE FROM candidates")
                    conn.execute("DELETE FROM environment_admins")
                    conn.execute("DELETE FROM environment_access")
                    for position, group in enumerate(state["groups"]):
                        group = deepcopy(group)
                        previous = previous_revisions.get(group["config_id"])
                        if previous is not None:
                            group["revision"] = max(group["revision"], previous + 1)
                        self._insert_group_row(conn, group, position)
                    for position, user in enumerate(state["environment_administrators"]):
                        conn.execute(
                            "INSERT INTO environment_admins(user_id, position) VALUES(?, ?)",
                            (user, position))
                    for position, candidate in enumerate(candidates):
                        conn.execute(
                            "INSERT INTO candidates(candidate_id, group_openid, member_openid, "
                            "nickname, note, created_at, expires_at, position) "
                            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                            (candidate["candidate_id"], candidate["group_openid"],
                             candidate["member_openid"], candidate["nickname"],
                             candidate["note"], candidate["created_at"],
                             candidate["expires_at"], position))
                    for role in ACCESS_LIST_KEYS:
                        for user in sorted(access[role]):
                            conn.execute(
                                "INSERT OR IGNORE INTO environment_access(role, user_id) "
                                "VALUES(?, ?)", (role, user))
                    self._insert_legacy_remarks(conn, remarks)
                    new_revision = max(current, state["revision"]) + 1
                    conn.execute("UPDATE meta SET value = ? WHERE key = 'revision'",
                                 (str(new_revision),))
            except BaseException:
                # 显式导入在库缺失时允许临时建库；导入失败必须删掉这个空库，
                # 否则下次普通打开会把 legacy/ 留档当成已恢复。
                if created_db:
                    self._close_connection()
                    for suffix in ("", "-wal", "-shm", "-journal"):
                        try:
                            Path(str(path) + suffix).unlink(missing_ok=True)
                        except OSError:
                            pass
                raise
            finally:
                self._allow_empty_rebuild = False
        imported_server_ids = {server["server_id"] for group in state["groups"]
                               for server in group["servers"]}
        for server_id in old_server_ids - imported_server_ids:
            try:
                self._console_token_path(environment, server_id).unlink(missing_ok=True)
            except OSError:
                pass
        return {"groups": len(state["groups"]), "servers": len(imported_server_ids),
                "revision": new_revision}

    # ---------- 写入：群与候选 ----------

    def _env_revision(self, conn):
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = 'revision'").fetchone()
            revision = int(row[0])
        except (KeyError, TypeError, ValueError, IndexError, sqlite3.Error):
            raise OfficialError("官方配置缺损或无法读取；请恢复后重试，不会读取旧群配置。") from None
        if revision < 0:
            raise OfficialError("官方配置缺损或无法读取；请恢复后重试，不会读取旧群配置。")
        return revision

    def _commit(self, conn, expected=None):
        """环境级修订号 +1；expected 用于旧调用方的乐观并发校验。"""
        current = self._env_revision(conn)
        if expected is not None and (type(expected) is not int or expected != current):
            raise OfficialError("配置已被其它操作修改，请刷新后重试。")
        conn.execute("UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
                     "WHERE key = 'revision'")

    def _bump_group(self, conn, config_id):
        conn.execute("UPDATE groups SET revision = revision + 1 WHERE config_id = ?",
                     (config_id,))

    @staticmethod
    def _revision(record, expected):
        if type(expected) is not int or expected != record["revision"]:
            raise OfficialError("配置已被其它操作修改，请刷新后重试。")

    def register(self, scope, note=""):
        note = text(note, 40)
        environment = uuid_value(scope.environment_id)
        now = self.clock()
        with self.lock, self._transaction(environment, write=True) as conn:
            conn.execute("DELETE FROM candidates WHERE expires_at <= ?", (now,))
            previous_row = conn.execute(
                "SELECT candidate_id, group_openid, member_openid, nickname, note, "
                "created_at, expires_at FROM candidates WHERE group_openid = ? AND "
                "member_openid = ? ORDER BY position, candidate_id LIMIT 1",
                (scope.group_id, scope.user_id)).fetchone()
            previous = None
            if previous_row is not None:
                previous = self._candidate_from_row(previous_row)
            if previous and now - previous["created_at"] < REGISTER_GAP:
                return previous
            distinct_row = conn.execute(
                "SELECT COUNT(*) FROM (SELECT group_openid FROM groups "
                "UNION SELECT group_openid FROM candidates WHERE expires_at > ?)", (now,)).fetchone()
            distinct = int(distinct_row[0])
            if scope.group_id not in {row[0] for row in conn.execute(
                    "SELECT group_openid FROM groups UNION SELECT group_openid FROM candidates WHERE expires_at > ?",
                    (now,))} and distinct >= MAX_GROUPS:
                raise OfficialError("待配置群数量已达上限，请联系后台管理员。")
            total = int(conn.execute(
                "SELECT COUNT(*) FROM candidates WHERE expires_at > ?", (now,)).fetchone()[0])
            in_group = int(conn.execute(
                "SELECT COUNT(*) FROM candidates WHERE group_openid = ? AND expires_at > ?",
                (scope.group_id, now)).fetchone()[0])
            if previous is None and (total >= MAX_CANDIDATES or in_group >= 20):
                raise OfficialError("登记候选已达上限，请联系后台管理员处理。")
            if previous is not None:
                conn.execute("DELETE FROM candidates WHERE candidate_id = ?",
                             (previous["candidate_id"],))
            position = int(conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM candidates").fetchone()[0])
            candidate = {
                "candidate_id": previous["candidate_id"] if previous else str(uuid4()),
                "group_openid": identifier(scope.group_id),
                "member_openid": identifier(scope.user_id),
                "nickname": scope.nickname, "note": note, "created_at": now,
                "expires_at": now + CANDIDATE_TTL,
            }
            conn.execute(
                "INSERT INTO candidates(candidate_id, group_openid, member_openid, nickname, "
                "note, created_at, expires_at, position) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (candidate["candidate_id"], candidate["group_openid"],
                 candidate["member_openid"], candidate["nickname"], candidate["note"],
                 candidate["created_at"], candidate["expires_at"], position))
            self._commit(conn)
        return candidate

    @staticmethod
    def _candidate_from_row(row):
        return {"candidate_id": row[0], "group_openid": row[1], "member_openid": row[2],
                "nickname": row[3], "note": row[4], "created_at": row[5],
                "expires_at": row[6]}

    def approve(self, environment, candidate_id, expected_revision, alias, display_group):
        environment = uuid_value(environment)
        uuid_value(candidate_id)
        with self.lock, self._transaction(environment, write=True) as conn:
            self._commit(conn, expected_revision)
            now = self.clock()
            row = conn.execute(
                "SELECT candidate_id, group_openid, member_openid, nickname, note, "
                "created_at, expires_at FROM candidates WHERE candidate_id = ? "
                "AND expires_at > ?", (candidate_id, now)).fetchone()
            if row is None:
                raise OfficialError("登记候选不存在或已过期，请重新登记。")
            candidate = self._candidate_from_row(row)
            groups = self._read_groups(conn, group_openid=candidate["group_openid"])
            if groups:
                return groups[0]
            group = {"config_id": str(uuid4()), "group_openid": candidate["group_openid"],
                     "revision": 1, "alias": alias_text(alias),
                     "display_group": display_group_text(display_group),
                     "enabled": False, "servers": [], "policy": self._new_policy(),
                     "administrators": [], "blacklist": [], "nickname_ignore": []}
            self._validate_group(group)
            position = int(conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM groups").fetchone()[0])
            self._insert_group_row(conn, group, position)
            return group

    def create_setup_code(self, environment, alias, display_group=""):
        """后台新增群时生成一次性验证码；只落哈希，明文仅在本次响应里返回。"""
        environment = uuid_value(environment)
        alias = alias_text(alias)
        display_group = display_group_text(display_group)
        with self.lock, self._transaction(environment, write=True) as conn:
            now = self.clock()
            conn.execute("DELETE FROM setup_codes WHERE expires_at < ?", (now - SETUP_KEEP,))
            pending = conn.execute(
                "SELECT code_hash FROM setup_codes WHERE used_at = 0 AND expires_at > ?",
                (now,)).fetchall()
            if len(pending) >= MAX_SETUP_CODES:
                raise OfficialError("待使用的验证码过多，请等待旧验证码过期后再试。")
            used = {entry[0] for entry in pending}
            code = ""
            for _ in range(20):
                candidate = "".join(secrets.choice(SETUP_ALPHABET) for _ in range(6))
                if _setup_hash(environment, candidate) not in used:
                    code = candidate
                    break
            if not code:
                raise OfficialError("生成验证码失败，请重试。")
            setup_id = str(uuid4())
            expires_at = now + SETUP_TTL
            conn.execute(
                "INSERT INTO setup_codes(setup_id, alias, display_group, code_hash, "
                "created_at, expires_at, used_at, config_id) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (setup_id, alias, display_group, _setup_hash(environment, code),
                 now, expires_at, 0, ""))
            return {"setup_id": setup_id, "code": code, "expires_at": expires_at, "now": now}

    def setup_status(self, environment, setup_id):
        environment = uuid_value(environment)
        setup_id = uuid_value(setup_id)
        with self.lock, self._transaction(environment, write=False) as conn:
            now = self.clock()
            row = conn.execute(
                "SELECT used_at, expires_at, config_id FROM setup_codes WHERE setup_id = ?",
                (setup_id,)).fetchone()
            if row is None:
                return {"status": "missing", "now": now}
            used_at, expires_at, config_id = row
            if used_at > 0:
                return {"status": "claimed", "config_id": config_id, "now": now}
            if expires_at <= now:
                return {"status": "expired", "expires_at": expires_at, "now": now}
            return {"status": "pending", "expires_at": expires_at, "now": now}

    def cancel_setup_code(self, environment, setup_id):
        environment = uuid_value(environment)
        setup_id = uuid_value(setup_id)
        with self.lock, self._transaction(environment, write=True) as conn:
            conn.execute("DELETE FROM setup_codes WHERE setup_id = ?", (setup_id,))

    def claim_setup_code(self, environment, code, group_openid):
        """群内 /mc setup <验证码> 原子消费验证码并创建群配置；同码不能再用。"""
        environment = uuid_value(environment)
        group_openid = identifier(group_openid)
        if not isinstance(code, str):
            raise OfficialError("验证码无效或已过期，请回后台重新生成。")
        code = code.strip().upper()
        if not SETUP_CODE_RE.fullmatch(code):
            raise OfficialError("验证码无效或已过期，请回后台重新生成。")
        digest = _setup_hash(environment, code)
        with self.lock, self._transaction(environment, write=True) as conn:
            now = self.clock()
            pending = conn.execute(
                "SELECT setup_id, alias, display_group, code_hash FROM setup_codes "
                "WHERE used_at = 0 AND expires_at > ?", (now,)).fetchall()
            matched = None
            for setup_id, alias, display_group, code_hash in pending:
                if hmac.compare_digest(code_hash, digest):
                    matched = (setup_id, alias, display_group)
                    break
            if matched is None:
                raise OfficialError("验证码无效或已过期，请回后台重新生成。")
            setup_id, alias, display_group = matched
            groups = self._read_groups(conn, group_openid=group_openid)
            created = not groups
            if created:
                count = int(conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0])
                if count >= MAX_GROUPS:
                    raise OfficialError("官方群数量已达上限，请先删除不再使用的群配置。")
                group = {"config_id": str(uuid4()), "group_openid": group_openid,
                         "revision": 1, "alias": alias, "display_group": display_group,
                         "enabled": False, "servers": [], "policy": self._new_policy(),
                         "administrators": [], "blacklist": [], "nickname_ignore": []}
                self._validate_group(group)
                position = int(conn.execute(
                    "SELECT COALESCE(MAX(position), 0) + 1 FROM groups").fetchone()[0])
                self._insert_group_row(conn, group, position)
            else:
                group = groups[0]
            conn.execute(
                "UPDATE setup_codes SET code_hash = '', used_at = ?, config_id = ? "
                "WHERE setup_id = ?", (now, group["config_id"], setup_id))
            if created:
                self._commit(conn)
        return group, created

    # ---------- 写入：权限与审计 ----------

    def authorize(self, environment, config_id, candidate_id, role, expected_revision):
        """只由已认证后台调用；候选本身不授予权限。"""
        environment = uuid_value(environment)
        uuid_value(config_id)
        uuid_value(candidate_id)
        if role not in ("member", "group_admin", "environment_admin", "blacklist", "nickname_ignore"):
            raise OfficialError("不支持的授权角色。")
        with self.lock, self._transaction(environment, write=True) as conn:
            groups = self._read_groups(conn, config_id=config_id)
            if not groups:
                raise OfficialError("未找到该官方环境的群配置。")
            group = groups[0]
            if role == "environment_admin":
                if type(expected_revision) is not int or expected_revision != self._env_revision(conn):
                    raise OfficialError("配置已被其它操作修改，请刷新后重试。")
            else:
                self._revision(group, expected_revision)
            now = self.clock()
            row = conn.execute(
                "SELECT candidate_id, group_openid, member_openid, nickname, note, "
                "created_at, expires_at FROM candidates WHERE candidate_id = ? "
                "AND group_openid = ? AND expires_at > ?",
                (candidate_id, group["group_openid"], now)).fetchone()
            if row is None:
                raise OfficialError("候选不属于该群或已经过期。")
            candidate = self._candidate_from_row(row)
            user = candidate["member_openid"]
            if role == "environment_admin":
                exists = conn.execute(
                    "SELECT 1 FROM environment_admins WHERE user_id = ?", (user,)).fetchone()
                if not exists:
                    count = int(conn.execute(
                        "SELECT COUNT(*) FROM environment_admins").fetchone()[0])
                    if count >= 1000:
                        raise OfficialError("环境管理员数量已达上限。")
                    position = int(conn.execute(
                        "SELECT COALESCE(MAX(position), 0) + 1 FROM environment_admins").fetchone()[0])
                    conn.execute(
                        "INSERT INTO environment_admins(user_id, position) VALUES(?, ?)",
                        (user, position))
            else:
                if role == "member":
                    conn.execute(
                        "DELETE FROM group_members WHERE config_id = ? AND role = 'administrators' "
                        "AND user_id = ?", (config_id, user))
                else:
                    field = {"group_admin": "administrators", "blacklist": "blacklist",
                             "nickname_ignore": "nickname_ignore"}[role]
                    exists = conn.execute(
                        "SELECT 1 FROM group_members WHERE config_id = ? AND role = ? "
                        "AND user_id = ?", (config_id, field, user)).fetchone()
                    if not exists:
                        position = int(conn.execute(
                            "SELECT COALESCE(MAX(position), 0) + 1 FROM group_members "
                            "WHERE config_id = ? AND role = ?",
                            (config_id, field)).fetchone()[0])
                        conn.execute(
                            "INSERT INTO group_members(config_id, role, user_id, position) "
                            "VALUES(?, ?, ?, ?)", (config_id, field, user, position))
                self._bump_group(conn, config_id)
            self._audit(conn, group, "grant", role, user, candidate_id)
            self._commit(conn)
            groups = self._read_groups(conn, config_id=config_id)
            if not groups:
                raise OfficialError("未找到该官方环境的群配置。")
            group = groups[0]
        return group

    def revoke(self, environment, config_id, user_id, expected_revision, role):
        environment = uuid_value(environment)
        uuid_value(config_id)
        identifier(user_id)
        if role not in ("group_admin", "environment_admin", "blacklist", "nickname_ignore"):
            raise OfficialError("请选择需要撤销的授权类型。")
        with self.lock, self._transaction(environment, write=True) as conn:
            groups = self._read_groups(conn, config_id=config_id)
            if not groups:
                raise OfficialError("未找到该官方环境的群配置。")
            group = groups[0]
            if role == "environment_admin":
                if type(expected_revision) is not int or expected_revision != self._env_revision(conn):
                    raise OfficialError("配置已被其它操作修改，请刷新后重试。")
                conn.execute("DELETE FROM environment_admins WHERE user_id = ?", (user_id,))
            else:
                self._revision(group, expected_revision)
                field = {"group_admin": "administrators", "blacklist": "blacklist",
                         "nickname_ignore": "nickname_ignore"}[role]
                conn.execute(
                    "DELETE FROM group_members WHERE config_id = ? AND role = ? AND user_id = ?",
                    (config_id, field, user_id))
                self._bump_group(conn, config_id)
            self._audit(conn, group, "revoke", role, user_id, None)
            self._commit(conn)
            groups = self._read_groups(conn, config_id=config_id)
            if not groups:
                raise OfficialError("未找到该官方环境的群配置。")
            group = groups[0]
        return group

    def _audit(self, conn, group, action, role, user_id, candidate_id):
        count = int(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
        if count >= 10000:
            raise OfficialError("授权记录已达容量上限，请安排维护后再修改授权。")
        conn.execute(
            "INSERT INTO audit_log(action, role, user_id, source_group, candidate_id, at, source) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (action, role, user_id, group["group_openid"], candidate_id, self.clock(),
             "authenticated_webui"))

    # ---------- 写入：群配置与服务器 ----------

    def update(self, environment, config_id, expected_revision, changes):
        environment = uuid_value(environment)
        uuid_value(config_id)
        if not isinstance(changes, dict) or set(changes) - {
                "alias", "display_group", "enabled", "servers", "policy", "nickname_ignore"}:
            raise OfficialError("群配置包含不支持的字段。")
        removed_servers = set()
        with self.lock, self._transaction(environment, write=True) as conn:
            groups = self._read_groups(conn, config_id=config_id)
            if not groups:
                raise OfficialError("未找到该官方环境的群配置。")
            group = groups[0]
            self._revision(group, expected_revision)
            previous_ids = {server["server_id"] for server in group["servers"]}
            changed = deepcopy(changes)
            if "alias" in changed:
                changed["alias"] = alias_text(changed["alias"])
            if "display_group" in changed:
                changed["display_group"] = display_group_text(changed["display_group"])
            if "servers" in changed:
                if not isinstance(changed["servers"], list) or len(changed["servers"]) > MAX_SERVERS:
                    raise OfficialError("每群最多配置 20 台服务器。")
                for server in changed["servers"]:
                    if not isinstance(server, dict):
                        raise OfficialError("服务器条目无效。")
                    if not server.get("server_id"):
                        server["server_id"] = str(uuid4())
                    elif server["server_id"] not in previous_ids:
                        raise OfficialError("新服务器请留空内部标识，不能复用外部服务器标识。")
            group.update(changed)
            self._validate_group(group)
            group["revision"] += 1
            conn.execute(
                "UPDATE groups SET alias = ?, display_group = ?, enabled = ?, revision = ?, "
                "policy_json = ? WHERE config_id = ?",
                (group["alias"], group["display_group"], 1 if group["enabled"] else 0,
                 group["revision"], self._policy_json(group["policy"]), config_id))
            if "nickname_ignore" in changed:
                conn.execute(
                    "DELETE FROM group_members WHERE config_id = ? AND role = 'nickname_ignore'",
                    (config_id,))
                for position, user in enumerate(group["nickname_ignore"]):
                    conn.execute(
                        "INSERT INTO group_members(config_id, role, user_id, position) "
                        "VALUES(?, 'nickname_ignore', ?, ?)", (config_id, user, position))
            if "servers" in changed:
                remaining = {server["server_id"] for server in group["servers"]}
                removed_servers = previous_ids - remaining
                if removed_servers:
                    placeholders = ",".join("?" for _ in removed_servers)
                    conn.execute(
                        f"DELETE FROM servers WHERE config_id = ? AND server_id IN ({placeholders})",
                        (config_id, *sorted(removed_servers)))
                for position, server in enumerate(group["servers"]):
                    exists = server["server_id"] in previous_ids
                    values = (server["name"], server["host"], server["game_port"],
                              server["api_port"], 1 if server["hidden"] else 0,
                              server["remark"], position)
                    if exists:
                        conn.execute(
                            "UPDATE servers SET name = ?, host = ?, game_port = ?, api_port = ?, "
                            "hidden = ?, remark = ?, position = ? WHERE server_id = ?",
                            (*values, server["server_id"]))
                    else:
                        conn.execute(
                            "INSERT INTO servers(server_id, config_id, name, host, game_port, "
                            "api_port, hidden, remark, position) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (server["server_id"], config_id, *values))
            self._commit(conn)
            groups = self._read_groups(conn, config_id=config_id)
            if not groups:
                raise OfficialError("未找到该官方环境的群配置。")
            group = groups[0]
        for server_id in removed_servers:
            try:
                self._console_token_path(environment, server_id).unlink(missing_ok=True)
            except OSError:
                pass
        return group

    def delete_group(self, environment, config_id, expected_revision):
        """只移除后台群配置；模组侧已有绑定与授权审计保持不动。"""
        environment = uuid_value(environment)
        uuid_value(config_id)
        with self.lock, self._transaction(environment, write=True) as conn:
            groups = self._read_groups(conn, config_id=config_id)
            if not groups:
                raise OfficialError("未找到该官方环境的群配置。")
            group = groups[0]
            self._revision(group, expected_revision)
            conn.execute("DELETE FROM groups WHERE config_id = ?", (config_id,))
            conn.execute("DELETE FROM candidates WHERE group_openid = ?",
                         (group["group_openid"],))
            self._commit(conn)
        for server in group["servers"]:
            try:
                self._console_token_path(environment, server["server_id"]).unlink(missing_ok=True)
            except OSError:
                pass
        return {"config_id": group["config_id"], "group_openid": group["group_openid"],
                "alias": group["alias"]}

    @staticmethod
    def _validate_group_shape(group):
        """列表页使用的轻量结构校验：不重复做 UUID/主机/名称正则。

        写入和点读仍调用 _validate_group；这里只确保页面不会因缺失字段或
        错误类型崩溃，并保留各列表/服务器的数量上限。
        """
        keys = {"config_id", "group_openid", "revision", "alias", "display_group", "enabled",
                "servers", "policy", "administrators", "blacklist", "nickname_ignore"}
        if (set(group) != keys or type(group["revision"]) is not int or group["revision"] < 1
                or type(group["enabled"]) is not bool
                or not isinstance(group["config_id"], str)
                or not isinstance(group["group_openid"], str)
                or not isinstance(group["alias"], str) or len(group["alias"]) > 40
                or not isinstance(group["display_group"], str) or len(group["display_group"]) > 40):
            raise OfficialError("群配置结构无效。")
        for field in GROUP_MEMBER_ROLES:
            values = group[field]
            if (not isinstance(values, list) or len(values) > 1000
                    or any(not isinstance(value, str) for value in values)
                    or len(values) != len(set(values))):
                raise OfficialError("官方名单结构无效。")
        policy = group["policy"]
        defaults = default_policy()
        if not isinstance(policy, dict) or set(policy) != set(defaults):
            raise OfficialError("群策略字段不完整。")
        for key, value in defaults.items():
            if type(value) is bool and type(policy[key]) is not bool:
                raise OfficialError("群策略开关必须为布尔值。")
        if policy["bind_nickname_rule"] not in ("paren", "equals", "contains", "template"):
            raise OfficialError("不支持的昵称格式。")
        template = policy["bind_nickname_template"]
        if not isinstance(template, str) or len(template) > 64:
            raise OfficialError("昵称模板无效。")
        if policy["bind_nickname_rule"] == "template" and template.count("{gameId}") != 1:
            raise OfficialError("昵称模板必须包含一个 {gameId}。")
        if not isinstance(group["servers"], list) or len(group["servers"]) > MAX_SERVERS:
            raise OfficialError("服务器数量超限。")
        for server in group["servers"]:
            if (not isinstance(server, dict)
                    or set(server) != {"server_id", "name", "host", "game_port", "api_port",
                                       "hidden", "remark"}
                    or not isinstance(server["server_id"], str)
                    or not isinstance(server["name"], str) or len(server["name"]) > 20
                    or not isinstance(server["host"], str) or len(server["host"]) > 253
                    or type(server["game_port"]) is not int
                    or not 1 <= server["game_port"] <= 65535
                    or (server["api_port"] is not None
                        and (type(server["api_port"]) is not int
                             or not 1 <= server["api_port"] <= 65535))
                    or type(server["hidden"]) is not bool
                    or not isinstance(server["remark"], str) or len(server["remark"]) > 80):
                raise OfficialError("服务器字段无效，不允许在普通条目中填入凭据。")

    @staticmethod
    def _validate_group(group):
        keys = {"config_id", "group_openid", "revision", "alias", "display_group", "enabled", "servers", "policy",
                "administrators", "blacklist", "nickname_ignore"}
        if set(group) != keys or type(group["revision"]) is not int or group["revision"] < 1 or type(group["enabled"]) is not bool:
            raise OfficialError("群配置结构无效。")
        uuid_value(group["config_id"])
        identifier(group["group_openid"])
        group["alias"] = text(group["alias"], 40, empty=True)
        group["display_group"] = text(group["display_group"], 40)
        for field in ("administrators", "blacklist", "nickname_ignore"):
            values = group[field]
            if not isinstance(values, list) or len(values) > 1000:
                raise OfficialError("官方名单结构无效。")
            for value in values:
                identifier(value)
            if len(values) != len(set(values)):
                raise OfficialError("官方名单包含重复身份。")
        policy = group["policy"]
        defaults = default_policy()
        if not isinstance(policy, dict) or set(policy) != set(defaults):
            raise OfficialError("群策略字段不完整。")
        for key, value in defaults.items():
            if type(value) is bool and type(policy[key]) is not bool:
                raise OfficialError("群策略开关必须为布尔值。")
        if policy["bind_nickname_rule"] not in ("paren", "equals", "contains", "template"):
            raise OfficialError("不支持的昵称格式。")
        template = text(policy["bind_nickname_template"], 64)
        if policy["bind_nickname_rule"] == "template" and template.count("{gameId}") != 1:
            raise OfficialError("昵称模板必须包含一个 {gameId}。")
        if not isinstance(group["servers"], list) or len(group["servers"]) > MAX_SERVERS:
            raise OfficialError("服务器数量超限。")
        ids, names = set(), set()
        for server in group["servers"]:
            if not isinstance(server, dict) or set(server) != {"server_id", "name", "host", "game_port", "api_port", "hidden", "remark"}:
                raise OfficialError("服务器字段无效，不允许在普通条目中填入凭据。")
            uuid_value(server["server_id"])
            name = text(server["name"], 20, empty=False)
            if any(c.isspace() for c in name) or any(c in name for c in "[]【】"):
                raise OfficialError("服务器名称不能含空格或方括号。")
            host = text(server["host"], 253, empty=False)
            if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", host) or ":" in host:
                raise OfficialError("主机仅填写域名或 IPv4，端口另行填写。")
            for field in ("game_port", "api_port"):
                port = server[field]
                if field == "api_port" and port is None:
                    continue
                if type(port) is not int or not 1 <= port <= 65535:
                    raise OfficialError("端口必须是 1–65535 的整数。")
            if type(server["hidden"]) is not bool:
                raise OfficialError("隐藏开关必须为布尔值。")
            text(server["remark"], 80)
            if server["server_id"] in ids or name in names:
                raise OfficialError("服务器名称或标识重复。")
            ids.add(server["server_id"])
            names.add(name)

    # ---------- 备注与控制台令牌 ----------

    @staticmethod
    def _remark_payload(data):
        try:
            if (not isinstance(data, dict)
                    or set(data) - {"remark_src", "remark_motd", "remark_motd_at", "remark_motd_runs"}
                    or not {"remark_src", "remark_motd", "remark_motd_at"} <= set(data)
                    or data["remark_src"] not in ("manual", "motd")
                    or not isinstance(data["remark_motd"], str)
                    or len(data["remark_motd"]) > 80
                    or any(ord(c) < 32 or ord(c) == 127 for c in data["remark_motd"])
                    or type(data["remark_motd_at"]) not in (int, float)):
                raise ValueError
            result = {"remark_src": data["remark_src"], "remark_motd": data["remark_motd"],
                      "remark_motd_at": data["remark_motd_at"]}
            runs = normalize_motd_runs(data.get("remark_motd_runs"))
            if runs:
                result["remark_motd_runs"] = runs
            return result
        except (KeyError, TypeError, ValueError):
            return dict(OfficialStore._REMARK_DEFAULT)

    @staticmethod
    def _remark_from_row(remark_src, remark_motd, remark_motd_at, runs_json):
        payload = {"remark_src": remark_src, "remark_motd": remark_motd,
                   "remark_motd_at": remark_motd_at}
        if runs_json:
            try:
                payload["remark_motd_runs"] = json.loads(runs_json)
            except (TypeError, ValueError, RecursionError):
                return dict(OfficialStore._REMARK_DEFAULT)
        return OfficialStore._remark_payload(payload)

    def server_remark(self, environment, server_id):
        """备注来源与 MOTD 缓存单独存放；坏记录按手填空备注处理，不拖垮整页配置。"""
        return self.server_remarks(environment, [server_id]).get(
            server_id, dict(self._REMARK_DEFAULT))

    def save_server_remark(self, environment, config_id, server_id, expected_revision, *,
                           remark_src=None, remark_motd=None, remark_motd_runs=None):
        """只写备注元数据，不改变群配置修订；服务器必须仍属于该群。"""
        environment = uuid_value(environment)
        uuid_value(config_id)
        uuid_value(server_id)
        with self.lock, self._transaction(environment, write=True) as conn:
            groups = self._read_groups(conn, config_id=config_id)
            if not groups:
                raise OfficialError("未找到该官方环境的群配置。")
            group = groups[0]
            self._revision(group, expected_revision)
            if not any(server["server_id"] == server_id for server in group["servers"]):
                raise OfficialError("未找到该服务器。")
            row = conn.execute(
                "SELECT remark_src, remark_motd, remark_motd_at, remark_motd_runs_json "
                "FROM server_remarks WHERE server_id = ?", (server_id,)).fetchone()
            current = (self._remark_from_row(*row) if row else dict(self._REMARK_DEFAULT))
            if remark_src is not None:
                if remark_src not in ("manual", "motd"):
                    raise OfficialError("不支持的备注来源。")
                current["remark_src"] = remark_src
            if remark_motd is not None:
                current["remark_motd"] = text(remark_motd, 80)
                current["remark_motd_at"] = int(self.clock()) if current["remark_motd"] else 0
            if remark_motd_runs is not None:
                runs = normalize_motd_runs(remark_motd_runs)
                if runs:
                    current["remark_motd_runs"] = runs
                else:
                    current.pop("remark_motd_runs", None)
            runs = current.get("remark_motd_runs")
            conn.execute(
                "INSERT INTO server_remarks(server_id, remark_src, remark_motd, "
                "remark_motd_at, remark_motd_runs_json) VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(server_id) DO UPDATE SET remark_src = excluded.remark_src, "
                "remark_motd = excluded.remark_motd, remark_motd_at = excluded.remark_motd_at, "
                "remark_motd_runs_json = excluded.remark_motd_runs_json",
                (server_id, current["remark_src"], current["remark_motd"],
                 current["remark_motd_at"],
                 json.dumps(runs, ensure_ascii=False, separators=(",", ":")) if runs else None))
        return current

    def save_console_token(self, environment, config_id, server_id, expected_revision, token):
        """每服独立的控制台命令令牌；对应模组 consoleToken，留空即关闭该服控制台。"""
        if not isinstance(token, str) or (token and (
                len(token) < 16 or len(token) > 1024
                or any(ord(c) <= 32 or ord(c) >= 127 for c in token))):
            raise OfficialError("命令 Token 必须为 16–1024 个不含空格的可见 ASCII 字符，清除时留空。")
        environment = uuid_value(environment)
        uuid_value(config_id)
        uuid_value(server_id)
        with self.lock, self._transaction(environment, write=False) as conn:
            groups = self._read_groups(conn, config_id=config_id)
            if not groups:
                raise OfficialError("未找到该官方环境的群配置。")
            group = groups[0]
            self._revision(group, expected_revision)
            if not any(server["server_id"] == server_id for server in group["servers"]):
                raise OfficialError("该服务器不属于当前官方群。")
        if token and self.token_is_set(environment) and token == self.runtime_token(environment):
            raise OfficialError("命令 Token 不能与全局模组 API Token 相同。")
        path = self._console_token_path(environment, server_id)
        if token:
            atomic_json(path, {"console_token": token}, limit=8192)
        else:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                raise OfficialError("清除命令 Token 失败，请刷新后确认。") from None
        return {"server_id": server_id, "has_token": bool(token)}

    def console_token_ids(self, environment):
        """只返回已配置命令 Token 的服务器标识，不读取令牌内容。"""
        environment = uuid_value(environment)
        directory = self._env_dir(environment) / "console_tokens"
        try:
            present = {path.stem for path in directory.iterdir()
                       if path.is_file() and path.suffix == ".json"}
        except OSError:
            present = set()
        if not present:
            return []
        with self.lock, self._transaction(environment, write=False) as conn:
            # 通常只有少数服务器配置了控制台令牌；先按目录筛选，避免在全量
            # 服务器表上做 JOIN/排序。令牌很多时再退回全表查询。
            if len(present) <= 900:
                placeholders = ",".join("?" for _ in present)
                rows = conn.execute(
                    "SELECT s.server_id FROM servers s JOIN groups g ON g.config_id = s.config_id "
                    f"WHERE s.server_id IN ({placeholders}) "
                    "ORDER BY g.position, s.position, s.server_id",
                    sorted(present)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT s.server_id FROM servers s JOIN groups g ON g.config_id = s.config_id "
                    "ORDER BY g.position, s.position, s.server_id").fetchall()
        return [row[0] for row in rows if row[0] in present]

    def runtime_token(self, environment):
        """仅已选环境的运行请求调用；值与 OneBot 的 mod_api_token 完全一致。"""
        return self._mod_token()

    def runtime_console_token(self, environment, server_id):
        """Separate per-server owner file; only a currently authorized write uses it."""
        environment = uuid_value(environment)
        server_id = uuid_value(server_id)
        with self.lock:
            path = self._console_token_path(environment, server_id)
            data = read_json(path, {}, limit=8192)
            token = data.get("console_token", "") if isinstance(data, dict) else None
            if not isinstance(token, str) or len(token) > 1024 or token and (
                    len(token) < 16 or any(ord(c) < 33 or ord(c) > 126 for c in token)):
                raise OfficialError("本服控制台令牌配置无效。")
            return token

    # ---------- 权限名单与全局 Token ----------

    def _new_policy(self):
        try:
            overrides = self.policy_defaults() if callable(self.policy_defaults) else None
        except Exception:
            overrides = None
        return default_policy(overrides)

    def _public_access_lists(self):
        """读取 OneBot 共用的公共名单；只读，官方命令不会改写这里。"""
        result = {key: frozenset() for key in ACCESS_LIST_KEYS}
        if not callable(self.access_provider):
            return result
        try:
            data = self.access_provider()
        except Exception:
            return result
        if not isinstance(data, dict):
            return result
        for key in ACCESS_LIST_KEYS:
            value = data.get(key, [])
            if isinstance(value, str):
                value = re.split(r"[,，\s]+", value)
            if not isinstance(value, (list, tuple, set)):
                value = []
            result[key] = frozenset(str(item).strip() for item in value if str(item).strip())
        return result

    @staticmethod
    def _read_environment_access(conn):
        """从一条已有事务读取本环境补充名单；坏数据失败关闭。"""
        rows = conn.execute(
            "SELECT role, user_id FROM environment_access").fetchall()
        result = {key: set() for key in ACCESS_LIST_KEYS}
        if len(rows) > MAX_ACCESS_ENTRIES * len(ACCESS_LIST_KEYS):
            raise OfficialError("官方群名单配置缺损或格式不受支持。")
        for role, user in rows:
            if role not in result:
                raise OfficialError("官方群名单配置缺损或格式不受支持。")
            result[role].add(identifier(user))
        for role, values in result.items():
            if len(values) > MAX_ACCESS_ENTRIES:
                raise OfficialError("官方群名单配置缺损或格式不受支持。")
        return {key: frozenset(values) for key, values in result.items()}

    def _load_official_access(self, environment):
        """本官方环境专有的补充名单；坏数据失败关闭，不回落到空名单。"""
        environment = uuid_value(environment)
        with self.lock, self._transaction(environment, write=False) as conn:
            return self._read_environment_access(conn)

    def access_lists(self, environment=None):
        """公共名单 + 本官方环境按 @成员 添加的补充名单。"""
        result = self._public_access_lists()
        if environment is None:
            return result
        official = self._load_official_access(environment)
        return {key: result[key] | official[key] for key in ACCESS_LIST_KEYS}

    def access_membership(self, environment, key, target):
        """返回目标是否在官方群补充名单 / OneBot 公共名单中，用于去重与移除提示。"""
        if key not in ACCESS_LIST_KEYS:
            raise OfficialError("名单类型无效。")
        target = identifier(target)
        official = self._load_official_access(environment)
        public = self._public_access_lists()
        return target in official[key], target in public[key]

    def update_official_access(self, environment, key, target, add):
        """只写本环境补充名单；不触碰 OneBot 共用的公共名单。"""
        if key not in ACCESS_LIST_KEYS:
            raise OfficialError("名单类型无效。")
        target = identifier(target)
        environment = uuid_value(environment)
        with self.lock, self._transaction(environment, write=True) as conn:
            current = {row[0] for row in conn.execute(
                "SELECT user_id FROM environment_access WHERE role = ?", (key,))}
            if add:
                if target in current:
                    return False
                if len(current) >= MAX_ACCESS_ENTRIES:
                    raise OfficialError(f"官方群名单最多 {MAX_ACCESS_ENTRIES} 项，请先清理。")
                conn.execute(
                    "INSERT INTO environment_access(role, user_id) VALUES(?, ?)",
                    (key, target))
            else:
                if target not in current:
                    return False
                conn.execute(
                    "DELETE FROM environment_access WHERE role = ? AND user_id = ?",
                    (key, target))
            return True

    def access_level(self, scope, group, environment_administrators=()):
        """官方权限判断的唯一入口：本群名单 + 公共名单 + 环境管理员。"""
        return permission(scope, group, environment_administrators,
                          self.access_lists(scope.environment_id))

    def _mod_token(self):
        """只从全局插件配置取值；官方目录不保存、不返回明文凭据。"""
        try:
            raw = self.token_provider()
        except Exception:
            raise OfficialError("全局模组 API Token 读取失败，请检查插件配置。") from None
        if not isinstance(raw, str) or len(raw) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in raw):
            raise OfficialError("全局模组 API Token 配置无效，请到「全局参数 → 公共参数」重新保存。")
        return raw

    def token_is_set(self, environment):
        """面板只展示全局配置是否已填；值本身始终来自插件配置。"""
        try:
            return bool(self._mod_token())
        except OfficialError:
            return False

def permission(scope, group, environment_administrators=(), access=None):
    """官方权限等级：本群黑名单 > 公共黑名单 > 环境管理员/公共超管 > 本群管理员/公共白名单 > 平台角色。"""
    access = access if isinstance(access, dict) else {}
    if scope.user_id in group["blacklist"]:
        return 0
    if scope.user_id in access.get("blacklist", ()):
        return 0
    if scope.user_id in environment_administrators or scope.user_id in access.get("super_admins", ()):
        return 4
    if scope.user_id in group["administrators"] or scope.user_id in access.get("whitelist_admins", ()):
        return 3
    policy = group["policy"]
    if (scope.role == "owner" and policy["enable_group_owner"]) or (scope.role == "admin" and policy["enable_group_admin"]):
        return 3
    return 1


class PageCache:
    """缓存有限分页与最低权限；新事件回复凭据不进入缓存。"""
    def __init__(self, *, clock=time.monotonic, max_bytes=128 * 1024 * 1024, max_entries=32, ttl=600):
        self.clock, self.max_bytes, self.max_entries, self.ttl = clock, max_bytes, max_entries, ttl
        self.entries = OrderedDict()

    def _prune(self):
        for key, value in list(self.entries.items()):
            if value["expires"] <= self.clock():
                self.entries.pop(key)

    def put(self, scope, revision, pages, text_pages=None, *, minimum_permission=1):
        self._prune()
        if type(minimum_permission) is not int or minimum_permission not in (1, 3, 4):
            raise OfficialError("分页权限无效。")
        text_pages = tuple(text_pages or ())
        if not pages or any(not isinstance(p, bytes) or len(p) > 4 * 1024 * 1024 for p in pages):
            if pages:
                raise OfficialError("图片体积超过限制。")
        if not pages and not text_pages:
            raise OfficialError("查询结果为空。")
        if any(not isinstance(p, str) or len(p) > 1800 for p in text_pages):
            raise OfficialError("文本分页超过限制。")
        size = sum(len(p) for p in pages) + sum(len(p.encode("utf-8")) for p in text_pages)
        if size > self.max_bytes:
            raise OfficialError("查询结果过大，请缩小范围。")
        self.entries.pop(scope.key, None)
        while self.entries and (len(self.entries) >= self.max_entries or sum(e["size"] for e in self.entries.values()) + size > self.max_bytes):
            self.entries.popitem(last=False)
        self.entries[scope.key] = {"revision": revision, "pages": tuple(pages),
                                   "text_pages": text_pages, "expires": self.clock() + self.ttl, "size": size,
                                   "minimum_permission": minimum_permission}

    def authorize(self, scope, revision, level):
        self._prune()
        entry = self.entries.get(scope.key)
        if entry is None or entry["revision"] != revision or level < entry["minimum_permission"]:
            self.entries.pop(scope.key, None)
            raise OfficialError("分页已过期或当前权限不足，请重新查询。")

    def get(self, scope, revision, page):
        self._prune()
        entry = self.entries.get(scope.key)
        if entry is None or entry["revision"] != revision:
            self.entries.pop(scope.key, None)
            raise OfficialError("分页已过期或权限/配置已变化，请重新查询。")
        if type(page) is not int or not 1 <= page <= len(entry["pages"]):
            raise OfficialError("页码超出当前结果范围。")
        return entry["pages"][page - 1], len(entry["pages"])

    def get_text(self, scope, revision, page):
        self._prune()
        entry = self.entries.get(scope.key)
        if entry is None or entry["revision"] != revision:
            self.entries.pop(scope.key, None)
            raise OfficialError("分页已过期或权限/配置已变化，请重新查询。")
        if type(page) is not int or not 1 <= page <= len(entry["text_pages"]):
            raise OfficialError("该查询没有可用的文本分页。")
        return entry["text_pages"][page - 1], len(entry["text_pages"])

    def clear(self):
        self.entries.clear()
