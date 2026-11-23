"""Single-attempt V2 mod HTTP client; never loads configuration or credentials.

Each call takes an explicit transient token and uses a new http.client connection.
No environment proxy/auth lookup, cookies, redirect, auth retry, legacy route,
or worker is used. TLS uses the caller OS's public trust roots. Call on the plugin
I/O executor, not the AstrBot event loop. Offline tests
inject connection objects and never run the default network connection factory.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
import http.client
import json
import math
import re
import ssl
import time
from urllib.parse import urlencode

from .official_core import OfficialError, identifier, uuid_value
from .official_bindings import BindingError, GAME_RE, MAX_EFFECTS, MAX_JSON_BYTES, _json, _revision
from .official_binding_service import BindingScope
from .official_protocol import MESSAGES


PREFIX = "/api/v2/binding/"
COMMON = {"protocol_version", "environment_id", "group_id", "server_id"}
DOMAIN = re.compile(r"(?=.{1,253}$)[A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?\Z")
ENDPOINTS = {
    "operations": ("GET",frozenset(),frozenset({"filter","limit"}),False),
    "capabilities": ("GET", frozenset(), frozenset(), False),
    "check": ("GET", frozenset(), frozenset({"game_id", "user_id", "limit", "cursor","view"}), False),
    "token/validate": ("POST", frozenset({"token_code"}), frozenset(), False),
    "token/reserve": ("POST", frozenset({"operation_id", "user_id", "game_key", "token_generation", "token_code"}), frozenset(), True),
    "token/finalize": ("POST", frozenset({"operation_id", "reservation_id", "decision"}), frozenset({"token_generation"}), True),
    "sync": ("POST", frozenset({"operation_id", "effects"}), frozenset(), True),
    "bind": ("POST", frozenset({"operation_id", "request_key", "user_id", "game_id", "token_generation", "policy_revision", "nickname_basis", "actor", "authorization"}), frozenset({"token_code","resume","cancel","target_server_ids"}), True),
    "unbind": ("POST", frozenset({"operation_id", "request_key", "kind", "target_user_id", "targets", "actor", "authorization"}), frozenset({"cancel","target_server_ids"}), True),
    "command": ("POST", frozenset({"operation_id","request_key","action","payload","config_revision"}), frozenset(), True),
    "settings/whitelist": ("POST", frozenset({"enabled"}), frozenset(), True),
}


class TransportError(BindingError):
    def __init__(self, code, category, *, may_have_executed=False):
        super().__init__(code, MESSAGES.get(code, MESSAGES["STORAGE_ERROR"]))
        self.category = category
        self.may_have_executed = may_have_executed


@dataclass(frozen=True)
class ModTarget:
    scope: BindingScope
    host: str = field(repr=False)
    port: int
    scheme: str = "http"

    def __post_init__(self):
        if (not isinstance(self.scope, BindingScope) or not isinstance(self.host, str)
                or not DOMAIN.fullmatch(self.host) or type(self.port) is not int
                or not 1 <= self.port <= 65535 or self.scheme not in ("http", "https")):
            raise BindingError("INVALID_INPUT", "模组连接目标格式无效。")
        # The authenticated configuration owner approves private/LAN targets.
        # Reject numeric shorthand but do not resolve DNS during construction.
        if re.fullmatch(r"[0-9.]+", self.host):
            import ipaddress
            try:
                ipaddress.IPv4Address(self.host)
            except ValueError:
                raise BindingError("INVALID_INPUT", "IPv4 地址必须为完整四段。") from None
        elif re.fullmatch(r"(?:0x[0-9a-fA-F]+|[0-9]+)(?:\.(?:0x[0-9a-fA-F]+|[0-9]+)){0,3}", self.host):
            raise BindingError("INVALID_INPUT", "IPv4 地址不能使用非标准写法。")


def network_connection(target, timeout):
    if target.scheme == "https":
        return http.client.HTTPSConnection(target.host, target.port, timeout=timeout,
                                           context=ssl.create_default_context())
    return http.client.HTTPConnection(target.host, target.port, timeout=timeout)


def _strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _fields(endpoint, request):
    if not isinstance(request, dict) or not isinstance(endpoint, str):
        raise BindingError("INVALID_INPUT", "V2 请求必须为对象。")
    request = deepcopy(request)
    operation = endpoint.startswith("operations/")
    if operation:
        uuid_value(endpoint[len("operations/"):])
        spec = ("GET", frozenset(), frozenset(), False)
    else:
        spec = ENDPOINTS.get(endpoint)
    if endpoint=="unbind" and "management_action" in request:
        spec=("POST",frozenset({"operation_id","management_action"}),frozenset(),True)
    if spec is None:
        raise BindingError("NOT_SUPPORTED", "不支持的模组 V2 端点。")
    method, required, optional, mutation = spec
    if not required <= set(request) or set(request) - required - optional:
        raise BindingError("INVALID_INPUT", "V2 请求字段不完整或无效。")
    for name in ("operation_id", "reservation_id", "token_generation"):
        if name in request:
            if name == "reservation_id" and request[name] is None:
                if endpoint != "token/finalize" or request.get("decision") != "ABORTED" or "token_generation" not in request:
                    raise BindingError("INVALID_INPUT", "仅已中止且明确代次的操作可确认未预占验证码。")
                continue
            uuid_value(request[name])
    for name in ("user_id", "target_user_id"):
        if name in request:
            identifier(request[name])
    if "token_code" in request and (not isinstance(request["token_code"], str)
                                      or not re.fullmatch(r"[0-9]{6}", request["token_code"])):
        raise BindingError("INVALID_INPUT", "验证码必须为六位数字。")
    for name in ("game_id", "game_key"):
        if name in request and (not isinstance(request[name], str) or not GAME_RE.fullmatch(request[name])
                                  or (name == "game_key" and request[name] != request[name].lower())):
            raise BindingError("INVALID_INPUT", "游戏名格式无效。")
    if endpoint == "check":
        roster=request.get("view")=="active_roster"
        if ("view" in request and (not roster or "game_id" in request or "user_id" in request)
                or not roster and ("game_id" in request)==("user_id" in request)):
            raise BindingError("INVALID_INPUT", "查询目标必须二选一。")
        limit = request.get("limit", 100)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise BindingError("INVALID_INPUT", "查询数量无效。")
        if "cursor" in request and (not isinstance(request["cursor"], str) or not 1 <= len(request["cursor"]) <= 512
                                       or any(ord(c) < 33 or ord(c) == 127 for c in request["cursor"])):
            raise BindingError("INVALID_INPUT", "查询游标无效。")
    if "decision" in request and request["decision"] not in ("COMMITTED", "ABORTED"):
        raise BindingError("INVALID_INPUT", "验证码确认决定无效。")
    for name in ("effects", "targets"):
        if name in request and (not isinstance(request[name], list) or not 1 <= len(request[name]) <= MAX_EFFECTS):
            raise BindingError("LIMIT_EXCEEDED", "操作目标数量无效。")
    if "request_key" in request and (not isinstance(request["request_key"], str)
                                       or not re.fullmatch(r"[a-f0-9]{64}", request["request_key"])):
        raise BindingError("INVALID_INPUT", "请求幂等键无效。")
    if "policy_revision" in request:
        if not isinstance(request["policy_revision"], str):
            raise BindingError("INVALID_INPUT", "修订号必须为字符串。")
        _revision(request["policy_revision"])
    if endpoint in ("bind", "unbind") and "management_action" not in request:
        actor, auth = request["actor"], request["authorization"]
        if (not isinstance(actor, dict) or set(actor) != {"kind", "id"}
                or actor["kind"] not in ("qq_member", "game_admin")
                or not isinstance(auth, dict) or set(auth) != {"scope", "policy_revision"}
                or auth["scope"] not in ("self", "group", "environment")
                or not isinstance(auth["policy_revision"], str)):
            raise BindingError("INVALID_INPUT", "授权声明无效。")
        identifier(actor["id"])
        _revision(auth["policy_revision"])
        if endpoint == "bind" and not request.get("cancel") and auth["policy_revision"] != request["policy_revision"]:
            raise BindingError("CONFLICT", "策略修订不一致。")
        if endpoint == "unbind" and request["kind"] not in ("unbind_game", "unbind_user"):
            raise BindingError("INVALID_INPUT", "解绑种类无效。")
        for name in ("resume","cancel"):
            if name in request and type(request[name]) is not bool:
                raise BindingError("INVALID_INPUT","恢复动作必须为明确布尔值。")
        if endpoint=="bind" and not ("token_code" in request or request.get("resume") or request.get("cancel")):
            raise BindingError("INVALID_INPUT","新绑定必须提供验证码。")
    if "management_action" in request and request["management_action"] not in ("cancel","retry"):
        raise BindingError("INVALID_INPUT","不支持的后台原操作动作。")
    if endpoint=="operations" and (request.get("filter","pending") not in ("pending","all")
            or type(request.get("limit",201)) is not int or not 1<=request.get("limit",201)<=201):
        raise BindingError("INVALID_INPUT","操作列表范围无效。")
    if endpoint=="command":
        _revision(request["config_revision"])
        if (request["action"] not in ("broadcast","console") or not isinstance(request["payload"],str)
                or not request["payload"].strip() or len(request["payload"])>(200 if request["action"]=="console" else 1000)
                or any(ord(c)<32 or ord(c)==127 for c in request["payload"])):
            raise BindingError("INVALID_INPUT","写命令内容无效。")
    if endpoint=="settings/whitelist" and type(request["enabled"]) is not bool:
        raise BindingError("INVALID_INPUT","登录开关必须为明确布尔值。")
    _json(request)
    return method, mutation, request


class V2ModClient:
    """A client pins one target. Token is passed per call and is never retained."""
    def __init__(self, target, *, connection_factory=network_connection, total_timeout=8,
                 connect_timeout=2, clock=time.monotonic):
        if (not isinstance(target, ModTarget) or not callable(connection_factory)
                or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n <= 0
                       for n in (total_timeout, connect_timeout)) or total_timeout > 30):
            raise ValueError("Explicit V2 target and bounded timeout required")
        self.target, self.connection_factory = target, connection_factory
        self.total_timeout, self.connect_timeout, self.clock = total_timeout, min(connect_timeout, total_timeout), clock

    def _remaining(self, deadline, connection, opened_sock=None):
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError
        sock = getattr(connection, "sock", None) or opened_sock
        if sock is not None:
            sock.settimeout(remaining)
        connection.timeout = remaining

    def request(self, endpoint, request, *, token, revalidate=None):
        method, mutation, data = _fields(endpoint, request)
        if (not isinstance(token, str) or not 1 <= len(token) <= 4096
                or any(ord(c) < 33 or ord(c) > 126 for c in token)):
            raise TransportError("UNAUTHORIZED", "missing_credential")
        data.update(self.target.scope.wire())
        encoded = _json(data).encode("utf-8")
        path = PREFIX + endpoint
        if method == "GET":
            path += "?" + urlencode(data)
        connection, response, dispatched = None, None, False
        deadline = self.clock() + self.total_timeout
        try:
            if revalidate is not None:
                revalidate()
            connection = self.connection_factory(self.target, self.connect_timeout)
            # Connect separately: pre-connect failures have no request side effect.
            connection.connect()
            connection.auto_open = 0  # No implicit reconnect if the socket disappears.
            connection.set_debuglevel(0)
            opened_sock = connection.sock
            self._remaining(deadline, connection, opened_sock)
            if revalidate is not None:
                revalidate()
            dispatched = True
            connection.request(method, path, body=encoded if method == "POST" else None,
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                         "Accept": "application/json", "Connection": "close"})
            self._remaining(deadline, connection)
            response = connection.getresponse()
            status = response.status
            if status in (401, 403):
                raise TransportError("UNAUTHORIZED", "http_auth")
            if status == 429:
                raise TransportError("DB_UNAVAILABLE", "rate_limited")
            if 300 <= status < 400:
                raise TransportError("RESULT_UNKNOWN" if mutation else "STORAGE_ERROR", "redirect",
                                     may_have_executed=mutation)
            length = response.getheader("Content-Length")
            if length is not None and (not re.fullmatch(r"[0-9]{1,12}", length) or int(length) > MAX_JSON_BYTES):
                raise TransportError("RESULT_UNKNOWN" if mutation else "LIMIT_EXCEEDED", "response_size",
                                     may_have_executed=mutation)
            raw = bytearray()
            while len(raw) <= MAX_JSON_BYTES:
                self._remaining(deadline, connection, opened_sock)
                # read1 permits deadline checks between available socket chunks.
                chunk = response.read1(min(65536, MAX_JSON_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) > MAX_JSON_BYTES:
                raise TransportError("RESULT_UNKNOWN" if mutation else "LIMIT_EXCEEDED", "response_size",
                                     may_have_executed=mutation)
            if length is not None and len(raw) != int(length):
                raise TransportError("RESULT_UNKNOWN" if mutation else "STORAGE_ERROR", "truncated_response",
                                     may_have_executed=mutation)
            if status == 404 and not raw:
                raise TransportError("NOT_SUPPORTED", "missing_v2_route")
            try:
                payload = _strict_json(bytes(raw))
            except (ValueError, UnicodeError, RecursionError):
                code = "NOT_SUPPORTED" if status == 404 and endpoint == "capabilities" else "RESULT_UNKNOWN" if mutation else "STORAGE_ERROR"
                raise TransportError(code, "invalid_json", may_have_executed=mutation) from None
            result = self._envelope(endpoint, status, payload, mutation)
            if "operation_id" in request and result.get("operation_id") != request["operation_id"]:
                raise TransportError("SCOPE_MISMATCH", "operation_id", may_have_executed=mutation)
            return result
        except TransportError:
            raise
        except BindingError:
            raise
        except OfficialError:
            raise TransportError("INVALID_INPUT", "local_validation") from None
        except Exception:
            raise TransportError("RESULT_UNKNOWN" if mutation and dispatched else "DB_UNAVAILABLE",
                                 "request_unknown" if dispatched else "connect_failed",
                                 may_have_executed=mutation and dispatched) from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def _envelope(self, endpoint, status, payload, mutation):
        unknown = "RESULT_UNKNOWN" if mutation else "STORAGE_ERROR"
        if (not isinstance(payload, dict) or set(payload) != {"success", "code", "message", "data"}
                or type(payload["success"]) is not bool or not isinstance(payload["code"], str)
                or payload["code"] not in MESSAGES
                or not isinstance(payload["message"], str) or not isinstance(payload["data"], dict)):
            raise TransportError(unknown, "invalid_envelope", may_have_executed=mutation)
        data, code, success = payload["data"], payload["code"], payload["success"]
        if type(data.get("protocol_version")) is not int or data["protocol_version"] != 2:
            raise TransportError("SCHEMA_MISMATCH", "protocol_version", may_have_executed=mutation)
        if "scope" in data and data["scope"] != self.target.scope.wire():
            raise TransportError("SCOPE_MISMATCH", "response_scope", may_have_executed=mutation)
        if endpoint in ("bind","unbind") and status==200 and not success and data.get("state") in ("REJECTED","ABORTED"):
            return deepcopy(data)
        if endpoint.startswith("operations/"):
            if data.get("operation_id") != endpoint[len("operations/"):] and status in (200, 202):
                raise TransportError("SCOPE_MISMATCH", "operation_id")
            if status == 200 and data.get("state") in ("REJECTED", "ABORTED") and not success:
                return deepcopy(data)
            if (status == 202 and code == "RESULT_UNKNOWN" and not success and data.get("center_available") is False
                    and data.get("scope") == self.target.scope.wire() and isinstance(data.get("reservation"), dict)):
                # This is a mod-local durable receipt, not proof of center commit.
                return deepcopy(data)
        if success:
            if status != 200 or code not in ("OK", "SYNC_PENDING", "ALREADY_BOUND"):
                raise TransportError(unknown, "inconsistent_success", may_have_executed=mutation)
            if endpoint in ("capabilities", "token/validate") and data.get("scope") != self.target.scope.wire():
                raise TransportError("SCOPE_MISMATCH", "missing_scope", may_have_executed=mutation)
            if endpoint.startswith("operations/") and data.get("operation_id") != endpoint[len("operations/"):]:
                raise TransportError("SCOPE_MISMATCH", "operation_id")
            if endpoint == "settings/whitelist" and (
                    set(data) != {"protocol_version", "scope", "enabled", "persisted",
                                  "online_players", "released"}
                    or type(data.get("enabled")) is not bool or type(data.get("persisted")) is not bool
                    or any(type(data.get(name)) is not int or data[name] < 0
                           for name in ("online_players", "released"))):
                raise TransportError("SCHEMA_MISMATCH", "whitelist_reply", may_have_executed=mutation)
            return deepcopy(data)
        expected_status = {"PREPARED": (202,), "RESERVED": (202,), "RESULT_UNKNOWN": (202,),
            "UNAUTHORIZED": (401, 403), "SCOPE_MISMATCH": (404, 409), "CONFLICT": (200, 409),
            "DB_UNAVAILABLE": (503,), "SCHEMA_MISMATCH": (503,), "STORAGE_ERROR": (503,),
            "NOT_SUPPORTED": (400, 404), "INVALID_INPUT": (400,), "TOKEN_INVALID": (400,),
            "SERVER_REQUIRED": (409,), "LIMIT_EXCEEDED": (400,)}
        if status not in expected_status.get(code, ()):
            raise TransportError(unknown, "inconsistent_failure", may_have_executed=mutation)
        # Only the stable code leaves the transport; remote message/data never
        # become exception text. Unknown business state is recovered by operation.
        if code in ("PREPARED", "RESERVED"):
            return deepcopy(data)
        raise TransportError(code, "business", may_have_executed=mutation and code == "RESULT_UNKNOWN")

    def handshake(self, *, token, storage_mode="hybrid", revalidate=None):
        if storage_mode not in ("hybrid", "remote", "local"):
            raise BindingError("INVALID_INPUT", "目标存储模式无效。")
        data = self.request("capabilities", {}, token=token, revalidate=revalidate)
        required = {"protocol_version", "scope", "storage_type", "storage_mode", "schema_version", "game_key_rule",
                    "multi_account", "operation_results", "revision_sync", "authority"}
        if (not required <= set(data) <= required | {"token_reservation_query"}
                or ("token_reservation_query" in data and type(data["token_reservation_query"]) is not bool)
                or data["storage_type"] != "qq_official" or data["storage_mode"] != storage_mode
                or type(data["schema_version"]) is not int or data["schema_version"] != 1
                or data["game_key_rule"] != "ascii_lower"
                or data["authority"] != ("local" if storage_mode == "local" else "center")
                or any(data[field] is not True for field in ("multi_account", "operation_results", "revision_sync"))):
            raise TransportError("SCHEMA_MISMATCH", "capability_mismatch")
        return data


class V2TokenPeer:
    """Wire-to-internal adapter. Handshake is explicit and never run on import.

    The host supplies token_source and an active-scope guard; the adapter retains
    neither token values nor token codes. Public reserve/finalize replies omit
    user_id, so the normalized identity is taken ONLY from the immutable local
    operation after scope, operation_id, generation and echoed game_key checks.

    Reservation queries require the explicit token_reservation_query capability.
    An older center-only operations reply can never prove a local reservation.
    """
    def __init__(self, client, token_source, *, revalidate, storage_mode="hybrid"):
        if (not isinstance(client, V2ModClient) or not callable(token_source) or not callable(revalidate)
                or storage_mode not in ("hybrid", "remote", "local")):
            raise ValueError("Explicit client, transient credential source and active guard required")
        self.client, self.token_source, self.revalidate, self.storage_mode = client, token_source, revalidate, storage_mode
        self.ready = False
        self.reservation_query = False

    def handshake(self):
        self.ready = False
        self.reservation_query = False
        data = self.client.handshake(token=self._token(), storage_mode=self.storage_mode,
                                     revalidate=self.revalidate)
        self.ready = True
        self.reservation_query = data.get("token_reservation_query") is True
        return deepcopy(data)

    def _token(self):
        self.revalidate()  # Check the active scope before consulting the credential owner.
        try:
            return self.token_source()
        except Exception:
            raise TransportError("UNAUTHORIZED", "credential_unavailable") from None

    def _request(self, scope, endpoint, payload):
        if not isinstance(scope, BindingScope) or scope != self.client.target.scope:
            raise BindingError("SCOPE_MISMATCH", "模组目标范围不一致。")
        if not self.ready:
            raise BindingError("NOT_SUPPORTED", "模组 V2 握手尚未通过。")
        return self.client.request(endpoint, payload, token=self._token(), revalidate=self.revalidate)

    @staticmethod
    def _operation(scope, operation):
        try:
            if (operation["environment_id"] != scope.environment_id or operation["group_id"] != scope.group_id
                    or operation["original_server_id"] != scope.server_id or operation["kind"] != "bind"):
                raise BindingError("SCOPE_MISMATCH", "操作不属于当前取码服务器。")
            uuid_value(operation["operation_id"])
            uuid_value(operation["token_generation"])
            identifier(operation["target_user_id"])
            key = operation["intent"]["game_key"]
            if not isinstance(key, str) or not GAME_RE.fullmatch(key) or key != key.lower():
                raise ValueError
            return key
        except BindingError:
            raise
        except (KeyError, TypeError, ValueError):
            raise BindingError("INVALID_INPUT", "操作范围或结构无效。") from None

    def validate(self, scope, token_code, server_id):
        if server_id != scope.server_id:
            raise BindingError("SCOPE_MISMATCH", "显式选服范围不一致。")
        data = self._request(scope, "token/validate", {"token_code": token_code})
        try:
            required = {"protocol_version", "scope", "game_id", "game_key", "token_generation", "expires_at"}
            if set(data) != required or not GAME_RE.fullmatch(data["game_id"]) or data["game_key"] != data["game_id"].lower():
                raise ValueError
            uuid_value(data["token_generation"])
            if not isinstance(data["expires_at"], str) or not data["expires_at"].endswith("Z"):
                raise ValueError
            datetime.fromisoformat(data["expires_at"][:-1] + "+00:00")
        except (KeyError, TypeError, ValueError):
            raise TransportError("STORAGE_ERROR", "validate_receipt") from None
        return dict(data, state="FOUND")

    def reserve(self, scope, operation, token_code):
        key = self._operation(scope, operation)
        if operation["state"] != "PREPARED" or not operation["followup"].get("token_reservation"):
            raise BindingError("CONFLICT", "必须持久认领预占后才能外发。")
        data = self._request(scope, "token/reserve", dict(operation_id=operation["operation_id"],
            user_id=operation["target_user_id"], game_key=key, token_generation=operation["token_generation"], token_code=token_code))
        try:
            required = {"protocol_version", "operation_id", "reservation_id", "token_generation", "state", "game_key"}
            if (not required <= set(data) <= required | {"scope"}
                    or data["state"] != "RESERVED" or data["token_generation"] != operation["token_generation"]
                    or data["game_key"] != key):
                raise ValueError
            uuid_value(data["reservation_id"])
        except (KeyError, TypeError, ValueError):
            raise TransportError("RESULT_UNKNOWN", "reserve_receipt", may_have_executed=True) from None
        # Scope here is trusted request context, not an invented server echo.
        return dict(data, scope=scope.wire(), user_id=operation["target_user_id"])

    def finalize(self, scope, operation, decision):
        key = self._operation(scope, operation)
        expected_decision = "COMMITTED" if operation["state"] == "COMMITTED" else "ABORTED"
        if operation["state"] not in ("COMMITTED", "REJECTED", "ABORTED") or decision != expected_decision:
            raise BindingError("CONFLICT", "验证码确认必须依据中心已确定的终态。")
        reservation = operation.get("reservation_id") or (operation["followup"].get("token_finalize") or {}).get("reservation_id")
        payload = dict(operation_id=operation["operation_id"], reservation_id=reservation, decision=decision)
        if reservation is None:
            if decision != "ABORTED" or not self.reservation_query:
                raise BindingError("NOT_SUPPORTED", "未预占代次的安全作废能力尚未确认。")
            payload["token_generation"] = operation["token_generation"]
        else:
            uuid_value(reservation)
        data = self._request(scope, "token/finalize", payload)
        expected = "CONSUMED" if decision == "COMMITTED" else "INVALIDATED"
        required = {"protocol_version", "operation_id", "token_generation", "token_state"}
        if (not required <= set(data) <= required | {"scope", "reservation_id"}
                or data.get("token_generation") != operation["token_generation"] or data.get("token_state") != expected):
            raise TransportError("RESULT_UNKNOWN", "finalize_receipt", may_have_executed=True)
        if reservation is None:
            reservation = uuid_value(data.get("reservation_id"))
        elif "reservation_id" in data and reservation != data["reservation_id"]:
            raise TransportError("CONFLICT", "finalize_reservation", may_have_executed=True)
        return dict(protocol_version=2, scope=scope.wire(), state=expected, operation_id=operation["operation_id"],
            token_generation=operation["token_generation"], reservation_id=reservation,
            game_key=key, user_id=operation["target_user_id"])

    def status(self, scope, operation):
        key = self._operation(scope, operation)
        if not self.reservation_query:
            raise BindingError("NOT_SUPPORTED", "对端未声明本服预占查询能力，保留原操作待确认。")
        data = self._request(scope, "operations/" + operation["operation_id"], {})
        receipt = data.get("reservation")
        fields = {"protocol_version", "scope", "operation_id", "state", "token_generation", "reservation_id", "game_key", "user_id"}
        if (type(data.get("center_available")) is not bool or not isinstance(receipt, dict) or set(receipt) != fields
                or type(receipt.get("protocol_version")) is not int or receipt["protocol_version"] != 2
                or receipt["scope"] != scope.wire() or receipt["operation_id"] != operation["operation_id"]
                or receipt["state"] not in ("ABSENT", "RESERVED", "CONSUMED", "INVALIDATED")):
            raise TransportError("STORAGE_ERROR", "local_reservation_receipt")
        if receipt["state"] == "ABSENT":
            if any(receipt[name] is not None for name in ("token_generation", "reservation_id", "game_key", "user_id")):
                raise TransportError("CONFLICT", "absent_reservation_context")
            return dict(receipt, token_generation=operation["token_generation"], game_key=key, user_id=operation["target_user_id"])
        if (receipt["token_generation"] != operation["token_generation"] or receipt["game_key"] != key
                or receipt["user_id"] != operation["target_user_id"]):
            raise TransportError("CONFLICT", "local_reservation_context")
        uuid_value(receipt["reservation_id"])
        known = operation.get("reservation_id") or (operation["followup"].get("token_finalize") or {}).get("reservation_id")
        if known is not None and known != receipt["reservation_id"]:
            raise TransportError("CONFLICT", "local_reservation_id")
        return deepcopy(receipt)


class V2MirrorDelivery:
    """One explicit committed-op delivery; no timer, resubmission, or fallback.

    The game-side sync response proves only its reported mirror/effect parts.
    The operation remains committed when transport or local ACK storage fails.
    The host must dispatch this on its I/O worker and supply an active-scope guard.
    """
    def __init__(self, store, peer):
        if (not isinstance(peer, V2TokenPeer) or any(not callable(getattr(store, name, None))
                for name in ("get_operation", "acknowledge"))):
            raise ValueError("Explicit center store and scoped V2 peer required")
        self.store, self.peer = store, peer

    def deliver(self, scope, operation_id):
        operation = None
        try:
            uuid_value(operation_id)
            if not isinstance(scope, BindingScope) or scope != self.peer.client.target.scope:
                raise BindingError("SCOPE_MISMATCH", "同步目标范围不一致。")
            self.peer.revalidate()
            operation = self.store.get_operation(scope.environment_id, operation_id, group_id=scope.group_id)
            if (operation is None or operation["state"] != "COMMITTED"
                    or scope.server_id not in operation["intent"]["target_server_ids"]):
                return {"success": False, "code": "SCOPE_MISMATCH", "operation_id": operation_id}
            data = self.peer._request(scope, "sync", dict(operation_id=operation_id, effects=operation["effects"]))
            required = {"protocol_version", "operation_id", "results"}
            if not required <= set(data) <= required | {"scope"}:
                raise TransportError("STORAGE_ERROR", "sync_receipt")
            effects, results = operation["effects"], data.get("results")
            expected = {item["game_key"]: item["revision"] for item in effects}
            if (not isinstance(results, list) or len(results) != len(effects)
                    or any(not isinstance(item, dict) or not isinstance(item.get("game_key"), str) for item in results)
                    or {item["game_key"] for item in results} != set(expected)):
                raise TransportError("CONFLICT", "sync_receipt_set")
            for item in results:
                if (item.get("requested_revision") != expected[item["game_key"]]
                        or not isinstance(item.get("effective_revision"), str)):
                    raise TransportError("CONFLICT", "sync_receipt_revision")
                _revision(item["effective_revision"])
            # The transaction store independently validates each status/revision
            # before merging ACKs; malformed/incomplete batches never partly ACK.
            operation = self.store.acknowledge(environment_id=scope.environment_id, group_id=scope.group_id,
                operation_id=operation_id, server_id=scope.server_id, results=results)
            return {"success": True, "code": operation["result_code"], "state": "COMMITTED",
                    "operation_id": operation_id,
                    "delivery": deepcopy(operation["followup"]["delivery"][scope.server_id])}
        except OfficialError as error:
            code = error.code if isinstance(error, BindingError) else "INVALID_INPUT"
            if operation is not None and operation["state"] == "COMMITTED":
                return {"success": True, "code": operation["result_code"], "state": "COMMITTED",
                        "operation_id": operation_id, "followup_code": code}
            return {"success": False, "code": code, "operation_id": operation_id}
        except Exception:
            if operation is not None and operation["state"] == "COMMITTED":
                return {"success": True, "code": operation["result_code"], "state": "COMMITTED",
                        "operation_id": operation_id, "followup_code": "STORAGE_ERROR"}
            return {"success": False, "code": "STORAGE_ERROR", "operation_id": operation_id}
