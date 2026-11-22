"""官方环境的纯逻辑与独立存储。无框架、网络或导入时文件访问。"""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
import time
from uuid import UUID, uuid4


MAX_BYTES = 8 * 1024 * 1024
MAX_GROUPS = 100
MAX_CANDIDATES = 1000
MAX_SERVERS = 20
CANDIDATE_TTL = 86400
REGISTER_GAP = 30
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


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


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


def default_policy():
    return {"enable_group_owner": True, "enable_group_admin": True,
            "bind_require_nickname": True, "bind_nickname_rule": "paren",
            "bind_nickname_template": "", "show_server_version": False, "show_server_latency": True,
            "show_last_online": False,"nickname_check_enabled":True}


class OfficialStore:
    """每环境一个原子提交单元，registry/candidates/groups 不跨文件提交。"""
    def __init__(self, root, *, clock=time.time):
        self.root = Path(root)
        self.clock = clock
        self.lock = RLock()

    def _path(self, environment):
        return self.root / uuid_value(environment) / "state.json"

    def _load(self, environment):
        data = read_json(self._path(environment), {"schema_version": 1, "environment_id": environment,
                         "revision": 0, "groups": [], "candidates": [],
                         "environment_administrators": [], "authorization_history": []})
        try:
            if (not isinstance(data, dict) or set(data) != {"schema_version", "environment_id", "revision",
                    "groups", "candidates", "environment_administrators", "authorization_history"}
                    or type(data["schema_version"]) is not int or data["schema_version"] != 1 or data["environment_id"] != environment
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
            raise OfficialError("官方配置结构无效；已暂停使用，请恢复有效记录。") from None
        return data

    def _commit(self, data):
        data["revision"] += 1
        atomic_json(self._path(data["environment_id"]), data)

    def _candidates(self, data):
        return [c for c in data["candidates"] if c["expires_at"] > self.clock()]

    def list_state(self, environment):
        with self.lock:
            data = self._load(environment)
            data["candidates"] = self._candidates(data)
            return deepcopy(data)

    def group(self, environment, *, group_id=None, config_id=None):
        data = self.list_state(environment)
        if config_id is not None:
            uuid_value(config_id)
        if group_id is not None:
            identifier(group_id)
        return next((g for g in data["groups"] if
                     (group_id is not None and g["group_openid"] == group_id)
                     or (config_id is not None and g["config_id"] == config_id)), None)

    def register(self, scope, note=""):
        note = text(note, 40)
        with self.lock:
            data = self._load(scope.environment_id)
            candidates = self._candidates(data)
            previous = next((c for c in candidates if c["group_openid"] == scope.group_id
                             and c["member_openid"] == scope.user_id), None)
            if previous and self.clock() - previous["created_at"] < REGISTER_GAP:
                return deepcopy(previous)
            distinct = {g["group_openid"] for g in data["groups"]} | {c["group_openid"] for c in candidates}
            if scope.group_id not in distinct and len(distinct) >= MAX_GROUPS:
                raise OfficialError("待配置群数量已达上限，请联系后台管理员。")
            if previous is None and (len(candidates) >= MAX_CANDIDATES or sum(c["group_openid"] == scope.group_id for c in candidates) >= 20):
                raise OfficialError("登记候选已达上限，请联系后台管理员处理。")
            candidate = {"candidate_id": previous["candidate_id"] if previous else str(uuid4()),
                         "group_openid": identifier(scope.group_id), "member_openid": identifier(scope.user_id),
                         "nickname": scope.nickname, "note": note, "created_at": self.clock(),
                         "expires_at": self.clock() + CANDIDATE_TTL}
            data["candidates"] = [c for c in candidates if c is not previous] + [candidate]
            self._commit(data)
            return deepcopy(candidate)

    def approve(self, environment, candidate_id, expected_revision, alias, display_group):
        uuid_value(candidate_id)
        with self.lock:
            data = self._load(environment)
            self._revision(data, expected_revision)
            candidate = next((c for c in self._candidates(data) if c["candidate_id"] == candidate_id), None)
            if candidate is None:
                raise OfficialError("登记候选不存在或已过期，请重新登记。")
            group = next((g for g in data["groups"] if g["group_openid"] == candidate["group_openid"]), None)
            if group is None:
                group = {"config_id": str(uuid4()), "group_openid": candidate["group_openid"], "revision": 1,
                         "alias": text(alias, 40, empty=False), "display_group": text(display_group, 40),
                         "enabled": False, "servers": [], "policy": default_policy(),
                         "administrators": [], "blacklist": [], "nickname_ignore": []}
                data["groups"].append(group)
            self._commit(data)
            return deepcopy(group)

    def authorize(self, environment, config_id, candidate_id, role, expected_revision):
        """只由已认证后台调用；候选本身不授予权限。"""
        if role not in ("member", "group_admin", "environment_admin", "blacklist", "nickname_ignore"):
            raise OfficialError("不支持的授权角色。")
        with self.lock:
            data = self._load(environment)
            group = self._find_group(data, config_id)
            self._revision(data if role == "environment_admin" else group, expected_revision)
            candidate = next((c for c in self._candidates(data) if c["candidate_id"] == candidate_id
                              and c["group_openid"] == group["group_openid"]), None)
            if candidate is None:
                raise OfficialError("候选不属于该群或已经过期。")
            user = candidate["member_openid"]
            if role == "environment_admin":
                if user not in data["environment_administrators"]:
                    if len(data["environment_administrators"]) >= 1000:
                        raise OfficialError("环境管理员数量已达上限。")
                    data["environment_administrators"].append(user)
            else:
                field = {"group_admin": "administrators", "blacklist": "blacklist",
                         "nickname_ignore": "nickname_ignore"}.get(role)
                if role == "member":
                    group["administrators"] = [u for u in group["administrators"] if u != user]
                elif user not in group[field]:
                    group[field].append(user)
                group["revision"] += 1
            self._validate_group(group)
            self._audit(data, group, "grant", role, user, candidate["candidate_id"])
            self._commit(data)
            return deepcopy(group)

    def revoke(self, environment, config_id, user_id, expected_revision, role):
        identifier(user_id)
        if role not in ("group_admin", "environment_admin", "blacklist", "nickname_ignore"):
            raise OfficialError("请选择需要撤销的授权类型。")
        with self.lock:
            data = self._load(environment)
            group = self._find_group(data, config_id)
            if role == "environment_admin":
                self._revision(data, expected_revision)
                data["environment_administrators"] = [u for u in data["environment_administrators"] if u != user_id]
            else:
                self._revision(group, expected_revision)
                field = {"group_admin": "administrators", "blacklist": "blacklist",
                         "nickname_ignore": "nickname_ignore"}[role]
                group[field] = [u for u in group[field] if u != user_id]
                group["revision"] += 1
            self._audit(data, group, "revoke", role, user_id, None)
            self._commit(data)
            return deepcopy(group)

    def _audit(self, data, group, action, role, user_id, candidate_id):
        if len(data["authorization_history"]) >= 10000:
            raise OfficialError("授权记录已达容量上限，请安排维护后再修改授权。")
        data["authorization_history"].append({"action": action, "role": role, "user_id": user_id,
            "source_group": group["group_openid"], "candidate_id": candidate_id,
            "at": self.clock(), "source": "authenticated_webui"})

    def update(self, environment, config_id, expected_revision, changes):
        if not isinstance(changes, dict) or set(changes) - {"alias", "display_group", "enabled", "servers", "policy","nickname_ignore"}:
            raise OfficialError("群配置包含不支持的字段。")
        with self.lock:
            data = self._load(environment)
            group = self._find_group(data, config_id)
            self._revision(group, expected_revision)
            previous_ids = {s["server_id"] for s in group["servers"]}
            changed = deepcopy(changes)
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
            self._commit(data)
            return deepcopy(group)

    @staticmethod
    def _revision(record, expected):
        if type(expected) is not int or expected != record["revision"]:
            raise OfficialError("配置已被其它操作修改，请刷新后重试。")

    @staticmethod
    def _find_group(data, config_id):
        uuid_value(config_id)
        group = next((g for g in data["groups"] if g["config_id"] == config_id), None)
        if group is None:
            raise OfficialError("未找到该官方环境的群配置。")
        return group

    @staticmethod
    def _validate_group(group):
        keys = {"config_id", "group_openid", "revision", "alias", "display_group", "enabled", "servers", "policy",
                "administrators", "blacklist", "nickname_ignore"}
        if set(group) != keys or type(group["revision"]) is not int or group["revision"] < 1 or type(group["enabled"]) is not bool:
            raise OfficialError("群配置结构无效。")
        uuid_value(group["config_id"])
        identifier(group["group_openid"])
        group["alias"] = text(group["alias"], 40, empty=False)
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
        if isinstance(policy,dict):
            # Backwards-compatible optional policy; no disk migration on reads.
            policy.setdefault("nickname_check_enabled",True)
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

    def token_is_set(self, environment):
        """普通页面只看独立标记，不读取真实凭据内容。"""
        path = self.root / uuid_value(environment) / "mod_token.json"
        return path.is_file()

    def save_token(self, environment, token):
        if not isinstance(token, str) or len(token) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise OfficialError("模组令牌必须为可见 ASCII 字符，清除时留空。")
        with self.lock:
            path = self.root / uuid_value(environment) / "mod_token.json"
            if token:
                atomic_json(path, {"mod_api_token": token}, limit=8192)
            else:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    raise OfficialError("清除令牌失败，请刷新后确认。") from None

    def runtime_token(self, environment):
        """仅已选环境的运行请求调用；测试使用新临时目录的虚构值。"""
        data = read_json(self.root / uuid_value(environment) / "mod_token.json", {}, limit=8192)
        token = data.get("mod_api_token", "") if isinstance(data, dict) else None
        if not isinstance(token, str) or len(token) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise OfficialError("官方环境的模组令牌配置无效。")
        return token

    def runtime_console_token(self,environment,server_id):
        """Separate per-server owner file; only a currently authorized write uses it."""
        path=self.root/uuid_value(environment)/"console_tokens"/(uuid_value(server_id)+".json")
        data=read_json(path,{},limit=8192)
        token=data.get("console_token","") if isinstance(data,dict) else None
        if not isinstance(token,str) or len(token)>1024 or token and (len(token)<16 or any(ord(c)<33 or ord(c)>126 for c in token)):
            raise OfficialError("本服控制台令牌配置无效。")
        return token


def permission(scope, group, environment_administrators=()):
    if scope.user_id in group["blacklist"]:
        return 0
    if scope.user_id in environment_administrators:
        return 4
    if scope.user_id in group["administrators"]:
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
