"""Unregistered V2 protocol boundary; no sockets/config/credentials are accessed.

The host supplies a pinned scope, authenticated authorization callbacks and an
explicit capability probe. /sync is downlink effects -> results; outbound ACK
merging stays an internal repository method and is never exposed at /sync.
"""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import re
from threading import RLock
import time
from uuid import uuid4

from .official_core import OfficialError, identifier, uuid_value
from .official_bindings import BindingError, GAME_RE, MAX_EFFECTS, _json, _revision


PROTOCOL_VERSION = 2
PREFIX = "/api/v2/binding/"
COMMON = {"protocol_version", "environment_id", "group_id", "server_id"}
MESSAGES = {
    "OK": "已完成。", "PREPARED": "操作已准备，等待确认。", "RESERVED": "验证码已预占，等待中心结果。",
    "SYNC_PENDING": "中心已提交，仍有目标待同步。", "RESULT_UNKNOWN": "结果待确认，请查询原操作。",
    "INVALID_INPUT": "请求字段无效。", "UNAUTHORIZED": "请求未获授权。", "NOT_SUPPORTED": "该能力尚未接入。",
    "SCOPE_MISMATCH": "未找到当前范围的目标。", "CONFLICT": "范围、版本或操作意图冲突。",
    "DB_UNAVAILABLE": "官方中心暂不可用，暂停变更。", "SCHEMA_MISMATCH": "官方中心结构不匹配。",
    "STORAGE_ERROR": "官方绑定服务暂不可用。", "LIMIT_EXCEEDED": "请求超出容量限制。",
    "TOKEN_INVALID": "验证码无效。", "SERVER_REQUIRED": "请明确指定服务器。", "ALREADY_BOUND": "该账号已绑定。",
}


@dataclass(frozen=True)
class Scope:
    environment_id: str
    group_id: str
    server_id: str

    def __post_init__(self):
        uuid_value(self.environment_id)
        identifier(self.group_id)
        uuid_value(self.server_id)

    def wire(self):
        return dict(protocol_version=2, environment_id=self.environment_id,
                    group_id=self.group_id, server_id=self.server_id)


def _response(scope, code, data=None, *, success=None, status=None):
    if code not in MESSAGES:
        code = "STORAGE_ERROR"
    statuses = {"OK": 200, "SYNC_PENDING": 200, "ALREADY_BOUND": 200,
                "PREPARED": 202, "RESERVED": 202, "RESULT_UNKNOWN": 202,
                "UNAUTHORIZED": 401, "SCOPE_MISMATCH": 404, "CONFLICT": 409,
                "DB_UNAVAILABLE": 503, "SCHEMA_MISMATCH": 503, "STORAGE_ERROR": 503,
                "SERVER_REQUIRED": 409}
    body = {"protocol_version": 2}
    if scope is not None:
        body["scope"] = scope.wire()
    if data is not None:
        body.update(data)
    return {"status": status or statuses.get(code, 400), "body": {
        "success": code in ("OK", "SYNC_PENDING", "ALREADY_BOUND") if success is None else success,
        "code": code, "message": MESSAGES[code], "data": body}}


class OfficialBindingProtocol:
    def __init__(self, store=None, *, expected_scope=None, authorize=None, visible=None,
                 query_authorize=None, capabilities=None, sync_handler=None, token_service=None,
                 clock=time.monotonic):
        self.store = store
        self.expected_scope = self.scope(expected_scope) if expected_scope is not None else None
        self.authorize, self.visible, self.query_authorize = authorize, visible, query_authorize
        self.capability_probe, self.sync_handler, self.token_service = capabilities, sync_handler, token_service
        self.clock, self.snapshots, self.lock = clock, OrderedDict(), RLock()

    @staticmethod
    def scope(request):
        if not isinstance(request, dict):
            raise BindingError("INVALID_INPUT", "请求必须为对象。")
        if type(request.get("protocol_version")) is not int or request["protocol_version"] != 2:
            raise BindingError("NOT_SUPPORTED", "协议版本不受支持。")
        return Scope(request.get("environment_id"), request.get("group_id"), request.get("server_id"))

    @staticmethod
    def _fields(request, required=(), optional=()):
        if not COMMON | set(required) <= set(request) or set(request) - COMMON - set(required) - set(optional):
            raise BindingError("INVALID_INPUT", "请求字段不完整或包含未知字段。")
        _json(request)

    def _capabilities(self, scope):
        if not callable(self.capability_probe):
            raise BindingError("NOT_SUPPORTED", "没有已核验的能力声明。")
        data = self.capability_probe(scope)
        fields = {"storage_type", "storage_mode", "schema_version", "game_key_rule", "multi_account",
                  "operation_results", "revision_sync", "authority", "scope", "protocol_version"}
        if (not isinstance(data, dict) or not fields <= set(data) <= fields | {"token_reservation_query"}
                or ("token_reservation_query" in data and type(data["token_reservation_query"]) is not bool)
                or data["scope"] != scope.wire()
                or type(data["protocol_version"]) is not int or data["protocol_version"] != 2
                or data["storage_type"] != "qq_official" or data["game_key_rule"] != "ascii_lower"
                or type(data["schema_version"]) is not int or data["schema_version"] != 1
                or data["storage_mode"] not in ("hybrid", "remote", "local")
                or data["authority"] != ("local" if data["storage_mode"] == "local" else "center")
                or any(type(data[name]) is not bool for name in ("multi_account", "operation_results", "revision_sync"))
                or (data["operation_results"] and self.store is None)
                or (data["revision_sync"] and not callable(self.sync_handler))
                # This Python center facade has no mod-local reservation store.
                # Only the mod's independent token API can advertise this flag.
                or data.get("token_reservation_query") is True):
            raise BindingError("SCHEMA_MISMATCH", "能力声明不完整或与已接入服务不一致。")
        return deepcopy(data)

    def _operation(self, scope, operation_id, request):
        if self.store is None:
            raise BindingError("NOT_SUPPORTED", "没有官方操作存储。")
        operation = self.store.get_operation(scope.environment_id, operation_id, group_id=scope.group_id)
        if operation is None:
            raise BindingError("SCOPE_MISMATCH", "未找到操作。")
        # The target's stable server ID is part of the protocol boundary, even for center partitions.
        targets = operation["intent"]["target_server_ids"]
        if scope.server_id not in targets and scope.server_id != operation["original_server_id"]:
            raise BindingError("SCOPE_MISMATCH", "该操作不属于目标服务器。")
        if not callable(self.visible) or self.visible(operation, scope, request) is not True:
            raise BindingError("UNAUTHORIZED", "无权查看该操作。")
        return operation

    def _operation_reply(self, scope, operation):
        data = {"operation_id": operation["operation_id"], "state": operation["state"],
                "kind": operation["kind"], "effects": deepcopy(operation["effects"]),
                "delivery": {server: {part: target[part] for part in ("mirror", "whitelist", "online")}
                    for server, target in operation["followup"]["delivery"].items()}}
        code = operation["result_code"] or "RESULT_UNKNOWN"
        if operation["state"] in ("PREPARED", "RESERVED"):
            code = operation["state"]
        return _response(scope, code, data, status=200 if operation["state"] in ("REJECTED", "ABORTED") else None,
                         success=operation["state"] == "COMMITTED" or code == "ALREADY_BOUND")

    def _query(self, scope, request):
        if self.store is None:
            raise BindingError("NOT_SUPPORTED", "没有官方绑定存储。")
        if ("game_id" in request) == ("user_id" in request):
            raise BindingError("INVALID_INPUT", "查询目标必须二选一。")
        if not callable(self.query_authorize):
            raise BindingError("UNAUTHORIZED", "没有身份查询授权。")
        principal = self.query_authorize(scope, deepcopy(request))
        if not isinstance(principal, str) or not principal:
            raise BindingError("UNAUTHORIZED", "身份查询未经授权。")
        identifier(principal)  # Trusted context identity; never accepted from request.actor.
        if "game_id" in request:
            if "cursor" in request or "limit" in request:
                raise BindingError("INVALID_INPUT", "单账号查询不分页。")
            return _response(scope, "OK", self.store.check_game(scope.environment_id, scope.group_id, request["game_id"]))
        user = identifier(request["user_id"])
        limit = request.get("limit", 100)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise BindingError("INVALID_INPUT", "分页大小无效。")
        key = (scope, principal, user, limit)
        cursor = request.get("cursor")
        with self.lock:
            now = self.clock()
            self.snapshots = OrderedDict((k, v) for k, v in self.snapshots.items() if v["expires"] > now)
            if cursor is None:
                rows = self.store.active_snapshot(scope.environment_id, scope.group_id, user)
                snapshot = {"key": key, "rows": rows, "expires": now + 600}
                sid, offset = str(uuid4()), 0
                if len(self.snapshots) >= 32:
                    self.snapshots.popitem(last=False)
                self.snapshots[sid] = snapshot
            else:
                if not isinstance(cursor, str) or not re.fullmatch(r"[0-9a-f-]{36}:[0-9]{1,4}", cursor):
                    raise BindingError("INVALID_INPUT", "分页游标无效。")
                sid, raw_offset = cursor.split(":")
                uuid_value(sid)
                snapshot, offset = self.snapshots.get(sid), int(raw_offset)
                if (snapshot is None or snapshot["key"] != key or offset < 1 or offset % limit
                        or offset >= len(snapshot["rows"])):
                    raise BindingError("CONFLICT", "分页已过期或范围不一致，请重新查询。")
            page = deepcopy(snapshot["rows"][offset:offset + limit])
            following = f"{sid}:{offset + limit}" if offset + limit < len(snapshot["rows"]) else None
            return _response(scope, "OK", {"bindings": page, "game_ids": [v["game_id"] for v in page], "next_cursor": following})

    def _sync(self, scope, request):
        # The contract's /sync receives effects, never acknowledgments.
        if not callable(self.sync_handler):
            raise BindingError("NOT_SUPPORTED", "模组镜像同步尚未接入。")
        capabilities = self._capabilities(scope)
        if capabilities["authority"] != "center" or not capabilities["revision_sync"]:
            raise BindingError("NOT_SUPPORTED", "当前权威不接收中心镜像。")
        operation = self._operation(scope, uuid_value(request["operation_id"]), request)
        effects = request["effects"]
        if (operation["state"] != "COMMITTED" or scope.server_id not in operation["intent"]["target_server_ids"]
                or not isinstance(effects, list) or not 1 <= len(effects) <= MAX_EFFECTS
                or effects != operation["effects"]):
            raise BindingError("CONFLICT", "同步载荷不等于已提交的固定变更。")
        result = self.sync_handler(scope, operation["operation_id"], deepcopy(effects))
        if (not isinstance(result, dict) or set(result) != {"protocol_version", "operation_id", "results"}
                or result["protocol_version"] != 2 or result["operation_id"] != operation["operation_id"]
                or not isinstance(result["results"], list) or len(result["results"]) != len(effects)):
            raise BindingError("STORAGE_ERROR", "同步回执格式无效。")
        expected = {effect["game_key"]: effect["revision"] for effect in effects}
        seen = set()
        for item in result["results"]:
            required = {"game_key", "result", "requested_revision", "effective_revision", "persisted", "whitelist", "online"}
            if (not isinstance(item, dict) or set(item) != required or not isinstance(item["game_key"], str)
                    or item["game_key"] not in expected or item["game_key"] in seen
                    or item["requested_revision"] != expected[item["game_key"]]
                    or not isinstance(item["effective_revision"], str)
                    or type(item["persisted"]) is not bool or item["result"] not in ("applied", "ignored", "conflict")
                    or any(item[part] not in ("PENDING", "APPLIED", "SUPERSEDED", "FAILED", "CONFLICT") for part in ("whitelist", "online"))):
                raise BindingError("STORAGE_ERROR", "同步回执范围或版本无效。")
            _revision(item["effective_revision"])
            seen.add(item["game_key"])
        return _response(scope, "OK", result)

    def dispatch(self, method, path, request, *, authenticated=False):
        if authenticated is not True:
            return _response(None, "UNAUTHORIZED")
        scope = None
        try:
            if not isinstance(method, str) or not isinstance(path, str):
                raise BindingError("INVALID_INPUT", "路径或方法格式无效。")
            scope = self.scope(request)
            if self.expected_scope is None:
                raise BindingError("NOT_SUPPORTED", "协议服务未固定目标范围。")
            if scope != self.expected_scope:
                raise BindingError("SCOPE_MISMATCH", "协议目标范围不一致。")
            if not callable(self.authorize) or self.authorize(scope, deepcopy(request)) is not True:
                raise BindingError("UNAUTHORIZED", "协议请求未授权。")
            if method == "GET" and path == PREFIX + "capabilities":
                self._fields(request)
                return _response(scope, "OK", self._capabilities(scope))
            if method == "GET" and path.startswith(PREFIX + "operations/"):
                self._fields(request)
                op_id = uuid_value(path[len(PREFIX + "operations/"):])
                return self._operation_reply(scope, self._operation(scope, op_id, request))
            if method == "GET" and path == PREFIX + "check":
                self._fields(request, optional={"game_id", "user_id", "limit", "cursor"})
                return self._query(scope, request)
            if method == "POST" and path == PREFIX + "sync":
                self._fields(request, {"operation_id", "effects"})
                return self._sync(scope, request)
            # Token and mutation endpoints remain closed until the real peer and
            # policy guard are integrated. Never guess a generic token callback.
            raise BindingError("NOT_SUPPORTED", "官方 V2 路径或方法尚未开放。")
        except BindingError as error:
            return _response(None, error.code)
        except OfficialError:
            return _response(None, "INVALID_INPUT")
        except Exception:
            return _response(None, "STORAGE_ERROR")
