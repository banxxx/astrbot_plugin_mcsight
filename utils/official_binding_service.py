"""Internal center workflow; dependencies are explicit, with no framework startup.

policy_guard(scope, intent) receives an internal policy_phase and no token code.
It holds its configuration lock through the enclosed work and may select the
nickname basis after the peer resolves the account. Peer receipts come from the
authenticated V2 adapter.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import re
import time

from .official_core import OfficialError, identifier, uuid_value
from .official_bindings import BindingError, GAME_RE, HEX_64, MAX_TARGETS, TERMINAL, _revision


@dataclass(frozen=True)
class BindingScope:
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


@dataclass(frozen=True)
class PolicyLease:
    revision: str
    nickname_basis: dict | None = None


def _scope(value):
    if isinstance(value, BindingScope):
        return value
    if not isinstance(value, dict):
        raise BindingError("INVALID_INPUT", "绑定范围无效。")
    return BindingScope(value.get("environment_id"), value.get("group_id"), value.get("server_id"))


class OfficialBindingService:
    def __init__(self, store, peer, *, policy_guard=None, clock=time.time):
        methods = ("prepare_bind", "claim_reservation", "reserve", "commit_bind", "get_operation",
                   "find_request", "acknowledge", "abort", "prepare_unbind", "commit_unbind", "defer")
        if any(not callable(getattr(store, name, None)) for name in methods):
            raise TypeError("Explicit official transaction store required")
        if peer is not None and any(not callable(getattr(peer, name, None))
                                    for name in ("validate", "reserve", "finalize", "status")):
            raise TypeError("Explicit token peer required")
        self.store, self.peer, self.policy_guard, self.clock = store, peer, policy_guard, clock

    @staticmethod
    def _result(code, operation=None, *, operation_id=None, followup_code=None):
        # A confirmed center commit stays successful even if delivery is unknown.
        if operation is not None and operation["state"] == "COMMITTED":
            if code not in ("OK", "SYNC_PENDING"):
                followup_code = code
            code = operation["result_code"]
        result = {"code": code, "success": operation is not None and operation["state"] == "COMMITTED",
                  "state": operation["state"] if operation is not None else None}
        if operation is not None:
            result.update(operation_id=operation["operation_id"], operation=deepcopy(operation))
        elif operation_id is not None:
            result["operation_id"] = operation_id
        if followup_code is not None:
            result["followup_code"] = followup_code
        return result

    @staticmethod
    def _code(error, default="STORAGE_ERROR"):
        return error.code if isinstance(error, BindingError) else default

    @contextmanager
    def _policy(self, scope, intent, phase):
        if not callable(self.policy_guard):
            raise BindingError("NOT_SUPPORTED", "当前授权策略检查尚未接入。")
        context = deepcopy(intent)
        context["policy_phase"] = phase
        with self.policy_guard(scope, context) as lease:
            if isinstance(lease, str):
                lease = PolicyLease(lease, intent.get("nickname_basis"))
            if not isinstance(lease, PolicyLease) or not isinstance(lease.revision, str):
                raise BindingError("INVALID_INPUT", "策略修订必须为十进制字符串。")
            _revision(lease.revision)
            yield lease

    @staticmethod
    def _request(scope, request):
        required = {"operation_id", "request_key", "actor_kind", "actor_id", "user_id",
                    "authorization", "policy_revision", "target_server_ids", "token_code"}
        if (not isinstance(request, dict) or not required <= set(request)
                or set(request) - required - {"nickname_basis", "server_id", "game_id"}):
            raise BindingError("INVALID_INPUT", "绑定请求字段不完整或无效。")
        values = deepcopy(request)
        uuid_value(values["operation_id"])
        if not isinstance(values["request_key"], str) or not HEX_64.fullmatch(values["request_key"]):
            raise BindingError("INVALID_INPUT", "请求幂等键无效。")
        if not isinstance(values["token_code"], str) or not re.fullmatch(r"[0-9]{6}", values["token_code"]):
            raise BindingError("INVALID_INPUT", "验证码必须为六位数字。")
        if values.get("server_id", scope.server_id) != scope.server_id:
            raise BindingError("SCOPE_MISMATCH", "选定服务器与绑定范围不一致。")
        if values.get("game_id") is not None and (not isinstance(values["game_id"], str)
                                                   or not GAME_RE.fullmatch(values["game_id"])):
            raise BindingError("INVALID_INPUT", "游戏名无效。")
        for field in ("actor_id", "user_id"):
            identifier(values[field])
        auth, revision = values["authorization"], values["policy_revision"]
        if (values["actor_kind"] not in ("qq_member", "game_admin") or not isinstance(revision, str)
                or not isinstance(auth, dict) or set(auth) != {"scope", "policy_revision"}
                or auth["scope"] not in ("self", "group", "environment") or auth["policy_revision"] != revision
                or (values["actor_kind"] == "qq_member" and values["actor_id"] != values["user_id"])
                or (values["actor_kind"] == "game_admin" and auth["scope"] == "self")):
            raise BindingError("UNAUTHORIZED", "绑定授权声明无效。")
        _revision(revision)
        targets = values["target_server_ids"]
        if not isinstance(targets, list) or not 1 <= len(targets) <= MAX_TARGETS:
            raise BindingError("LIMIT_EXCEEDED", "同步目标数量无效。")
        for target in targets:
            uuid_value(target)
        if len(set(targets)) != len(targets) or scope.server_id not in targets:
            raise BindingError("SCOPE_MISMATCH", "同步目标必须包含取码服务器且不能重复。")
        values["target_server_ids"] = sorted(targets)
        basis = values.get("nickname_basis")
        if basis is not None:
            if (not isinstance(basis, dict) or set(basis) != {"game_key", "expected_revision"}
                    or not isinstance(basis["game_key"], str) or not GAME_RE.fullmatch(basis["game_key"])
                    or basis["game_key"] != basis["game_key"].lower()
                    or not isinstance(basis["expected_revision"], str)):
                raise BindingError("INVALID_INPUT", "昵称校验依据无效。")
            _revision(basis["expected_revision"])
        return values

    def _peer(self, method, scope, *args):
        if self.peer is None:
            raise BindingError("NOT_SUPPORTED", "模组验证码服务尚未接入。")
        try:
            result = getattr(self.peer, method)(scope, *args)
        except BindingError:
            raise
        except Exception:
            raise BindingError("RESULT_UNKNOWN", "模组请求结果待确认。") from None
        states = {"validate": {"FOUND", "TOKEN_INVALID", "SERVER_REQUIRED", "UNKNOWN", "UNSUPPORTED"},
                  "reserve": {"RESERVED", "TOKEN_INVALID", "UNKNOWN", "UNSUPPORTED"},
                  "finalize": {"CONSUMED", "INVALIDATED", "UNKNOWN", "UNSUPPORTED"},
                  "status": {"ABSENT", "RESERVED", "CONSUMED", "INVALIDATED", "UNKNOWN", "UNSUPPORTED", "TOKEN_INVALID"}}
        if not isinstance(result, dict) or result.get("state") not in states[method]:
            raise BindingError("STORAGE_ERROR", "模组回执状态无效。")
        if result.get("scope") != scope.wire():
            raise BindingError("SCOPE_MISMATCH", "模组回执范围不一致。")
        if result["state"] in ("UNKNOWN", "UNSUPPORTED", "TOKEN_INVALID", "SERVER_REQUIRED"):
            code = {"UNKNOWN": "RESULT_UNKNOWN", "UNSUPPORTED": "NOT_SUPPORTED"}.get(result["state"], result["state"])
            raise BindingError(code, "模组操作尚未成功确认。")
        uuid_value(result.get("token_generation"))
        if method != "validate":
            operation = args[0]
            if (result.get("operation_id") != operation["operation_id"]
                    or result["token_generation"] != operation["token_generation"]
                    or result.get("game_key") != operation["intent"]["game_key"]
                    or result.get("user_id") != operation["target_user_id"]):
                raise BindingError("CONFLICT", "模组回执与原操作不一致。")
            if result["state"] == "ABSENT":
                if method != "status" or result.get("reservation_id") is not None:
                    raise BindingError("CONFLICT", "未预占回执结构无效。")
                return result
            uuid_value(result.get("reservation_id"))
            known = operation.get("reservation_id") or (operation["followup"].get("token_finalize") or {}).get("reservation_id")
            if known is not None and known != result["reservation_id"]:
                raise BindingError("CONFLICT", "模组预占标识与原操作不一致。")
        return result

    @staticmethod
    def _same_request(operation, scope, request):
        intent = operation["intent"]
        if (operation["kind"] != "bind" or operation["original_server_id"] != scope.server_id
                or operation["actor_kind"] != request["actor_kind"] or operation["actor_id"] != request["actor_id"]
                or operation["target_user_id"] != request["user_id"] or operation["policy_revision"] != request["policy_revision"]
                or intent["authorization"] != request["authorization"]
                or intent["target_server_ids"] != request["target_server_ids"]
                or (request.get("nickname_basis") is not None
                    and (operation["nickname_basis"] or None) != request["nickname_basis"])
                or (request.get("game_id") is not None and intent["game_id"] != request["game_id"])):
            raise BindingError("CONFLICT", "原操作与本次请求意图不一致。")

    def bind(self, scope, request):
        operation, op_id = None, None
        try:
            scope = _scope(scope)
            request = self._request(scope, request)
            op_id = request["operation_id"]
            safe_intent = {k: v for k, v in request.items() if k != "token_code"}
            safe_intent["kind"] = "bind"
            with self._policy(scope, safe_intent, "submit") as lease:
                if lease.revision != request["policy_revision"]:
                    raise BindingError("CONFLICT", "当前策略已变化。")
            existing = self.store.find_request(scope.environment_id, scope.group_id, request["request_key"])
            if existing is None:
                existing = self.store.get_operation(scope.environment_id, op_id, group_id=scope.group_id)
                if existing is not None and existing["request_key"] != request["request_key"]:
                    raise BindingError("CONFLICT", "操作编号已用于其它请求。")
            if existing is not None:
                self._same_request(existing, scope, request)
                if existing["state"] in TERMINAL:
                    return self._result(existing["result_code"], existing)
                return self.recover(scope, existing["operation_id"])
            match = self._peer("validate", scope, request["token_code"], scope.server_id)
            game = match.get("game_id")
            if (not isinstance(game, str) or not GAME_RE.fullmatch(game) or match.get("game_key") != game.lower()
                    or (request.get("game_id") is not None and game != request["game_id"])):
                raise BindingError("CONFLICT", "验证码账号与请求不一致。")
            expires = match.get("expires_at")
            if not isinstance(expires, str) or not expires.endswith("Z"):
                raise BindingError("STORAGE_ERROR", "验证码期限格式无效。")
            if datetime.fromisoformat(expires[:-1] + "+00:00").timestamp() <= self.clock():
                raise BindingError("TOKEN_INVALID", "验证码已过期。")
            safe_intent["game_id"] = game
            with self._policy(scope, safe_intent, "prepare") as lease:
                if lease.revision != request["policy_revision"]:
                    raise BindingError("CONFLICT", "当前策略已变化。")
                operation = self.store.prepare_bind(operation_id=op_id, request_key=request["request_key"],
                    environment_id=scope.environment_id, group_id=scope.group_id, actor_kind=request["actor_kind"],
                    actor_id=request["actor_id"], user_id=request["user_id"], server_id=scope.server_id,
                    token_generation=match["token_generation"], game_id=game, policy_revision=lease.revision,
                    target_server_ids=request["target_server_ids"], authorization=request["authorization"],
                    nickname_basis=lease.nickname_basis)
                op_id = operation["operation_id"]
                claimed = False
                if operation["state"] == "PREPARED" and not operation["followup"].get("token_reservation"):
                    operation, claimed = self.store.claim_reservation(environment_id=scope.environment_id,
                        group_id=scope.group_id, operation_id=op_id, token_generation=operation["token_generation"])
            if not claimed:
                return self.recover(scope, op_id)
            receipt = self._peer("reserve", scope, operation, request["token_code"])
            operation = self.store.reserve(environment_id=scope.environment_id, group_id=scope.group_id,
                operation_id=op_id, reservation_id=receipt["reservation_id"], token_generation=receipt["token_generation"])
            return self._commit(scope, operation)
        except OfficialError as error:
            return self._failure(scope, op_id, operation, error)
        except Exception:
            return self._result("STORAGE_ERROR", operation, operation_id=op_id)

    def _failure(self, scope, op_id, operation, error):
        code = self._code(error, "INVALID_INPUT")
        if op_id and code == "RESULT_UNKNOWN":
            try:
                latest = self.store.get_operation(scope.environment_id, op_id, group_id=scope.group_id)
                if latest is not None:
                    operation = latest
            except Exception:
                pass
        if operation is not None:
            try:
                self.store.defer(environment_id=scope.environment_id, group_id=scope.group_id, operation_id=operation["operation_id"])
            except Exception:
                pass
        return self._result(code, operation, operation_id=op_id)

    def _commit(self, scope, operation):
        committing = False
        try:
            with self._policy(scope, operation, "commit") as lease:
                committing = True
                operation = self.store.commit_bind(environment_id=scope.environment_id, group_id=scope.group_id,
                    operation_id=operation["operation_id"], policy_revision=lease.revision)
        except BindingError as error:
            if not committing and error.code in ("UNAUTHORIZED", "CONFLICT"):
                operation = self.store.abort(environment_id=scope.environment_id, group_id=scope.group_id,
                                            operation_id=operation["operation_id"], result_code=error.code)
            else:
                return self._failure(scope, operation["operation_id"], operation, error)
        try:
            return self._settle(scope, operation)
        except OfficialError as error:
            return self._failure(scope, operation["operation_id"], operation, error)

    def _settle(self, scope, operation, receipt=None):
        token = operation["followup"].get("token_finalize")
        if not token or token["state"] != "PENDING":
            return self._result(operation["result_code"], operation)
        decision = "COMMITTED" if operation["state"] == "COMMITTED" else "ABORTED"
        expected = "CONSUMED" if decision == "COMMITTED" else "INVALIDATED"
        if receipt is not None and receipt["state"] == "ABSENT" and decision == "COMMITTED":
            raise BindingError("STORAGE_ERROR", "中心已提交但本服预占缺失，请核对存储。")
        if receipt is not None and receipt["state"] == expected:
            operation = self.store.acknowledge(environment_id=scope.environment_id, group_id=scope.group_id,
                operation_id=operation["operation_id"], server_id=scope.server_id, token_state=expected,
                reservation_id=receipt["reservation_id"], token_generation=receipt["token_generation"])
            return self._result(operation["result_code"], operation)
        if token.get("reservation_id") is None:
            receipt = receipt or self._peer("status", scope, operation)
            if receipt["state"] == "ABSENT":
                if operation["state"] == "COMMITTED":
                    raise BindingError("STORAGE_ERROR", "中心已提交但本服预占缺失，请核对存储。")
            else:
                operation = deepcopy(operation)
                operation["reservation_id"] = receipt["reservation_id"]
        receipt = self._peer("finalize", scope, operation, decision)
        if receipt["state"] != expected:
            raise BindingError("CONFLICT", "验证码确认与中心终态不一致。")
        operation = self.store.acknowledge(environment_id=scope.environment_id, group_id=scope.group_id,
            operation_id=operation["operation_id"], server_id=scope.server_id, token_state=expected,
            reservation_id=receipt["reservation_id"], token_generation=receipt["token_generation"])
        return self._result(operation["result_code"], operation)

    def recover(self, scope, operation_id, token_code=None):
        operation, op_id = None, None
        try:
            scope, op_id = _scope(scope), uuid_value(operation_id)
            operation = self._visible_operation(scope, op_id, "recover")
            if token_code is not None and (not isinstance(token_code,str) or not re.fullmatch(r"[0-9]{6}",token_code)):
                raise BindingError("INVALID_INPUT", "恢复验证码必须为六位数字。")
            if operation["kind"] != "bind":
                if operation["state"] == "PREPARED":
                    with self._policy(scope, operation, "commit") as lease:
                        operation = self.store.commit_unbind(environment_id=scope.environment_id,
                            group_id=scope.group_id, operation_id=op_id, policy_revision=lease.revision)
                return self._result(operation["result_code"] or operation["state"], operation)
            receipt = None
            if operation["state"] == "PREPARED":
                if not operation["followup"].get("token_reservation"):
                    if token_code is None:
                        return self._result("PREPARED", operation)
                    match=self._peer("validate",scope,token_code,scope.server_id)
                    if (match.get("token_generation")!=operation["token_generation"]
                            or match.get("game_key")!=operation["intent"]["game_key"]):
                        raise BindingError("CONFLICT","恢复码不属于原操作的同一账号和代次。")
                    with self._policy(scope,operation,"commit") as lease:
                        if lease.revision!=operation["policy_revision"]:
                            raise BindingError("CONFLICT","原操作策略已变化，请中止后重新取码。")
                        operation,claimed=self.store.claim_reservation(environment_id=scope.environment_id,
                            group_id=scope.group_id,operation_id=op_id,token_generation=operation["token_generation"])
                    if claimed:
                        receipt=self._peer("reserve",scope,operation,token_code)
                        operation=self.store.reserve(environment_id=scope.environment_id,group_id=scope.group_id,
                            operation_id=op_id,reservation_id=receipt["reservation_id"],token_generation=receipt["token_generation"])
                        return self._commit(scope,operation)
                receipt = self._peer("status", scope, operation)
                if receipt["state"] in ("ABSENT", "INVALIDATED"):
                    with self._policy(scope, operation, "recover"):
                        operation = self.store.abort(environment_id=scope.environment_id, group_id=scope.group_id, operation_id=op_id)
                    return self._settle(scope, operation, receipt)
                if receipt["state"] != "RESERVED":
                    return self._result("CONFLICT", operation)
                with self._policy(scope, operation, "recover"):
                    operation = self.store.reserve(environment_id=scope.environment_id, group_id=scope.group_id,
                        operation_id=op_id, reservation_id=receipt["reservation_id"], token_generation=receipt["token_generation"])
            if operation["state"] == "RESERVED":
                receipt = receipt or self._peer("status", scope, operation)
                if receipt["state"] in ("ABSENT", "INVALIDATED"):
                    with self._policy(scope, operation, "recover"):
                        operation = self.store.abort(environment_id=scope.environment_id, group_id=scope.group_id, operation_id=op_id)
                    return self._settle(scope, operation, receipt)
                if receipt["state"] != "RESERVED":
                    raise BindingError("CONFLICT", "本服预占与中心未决状态不一致。")
                return self._commit(scope, operation)
            token = operation["followup"].get("token_finalize")
            receipt = self._peer("status", scope, operation) if token and token["state"] == "PENDING" else None
            return self._settle(scope, operation, receipt)
        except OfficialError as error:
            return self._failure(scope, op_id, operation, error)
        except Exception:
            return self._result("STORAGE_ERROR", operation, operation_id=op_id)

    def _visible_operation(self, scope, operation_id, phase):
        with self._policy(scope, {}, "access"):
            row = self.store.get_operation(scope.environment_id, operation_id, group_id=scope.group_id)
            if (row is None or row["kind"] == "bind" and row["original_server_id"] != scope.server_id
                    or row["kind"] != "bind" and scope.server_id not in row["intent"]["target_server_ids"]):
                raise BindingError("SCOPE_MISMATCH", "未找到当前范围的操作。")
            with self._policy(scope, row, phase):
                return row

    def inspect(self, scope, operation_id):
        """Read one visible operation without consulting or modifying the mod."""
        try:
            operation = self._visible_operation(_scope(scope), uuid_value(operation_id), "inspect")
            return self._result(operation["result_code"] or operation["state"], operation)
        except OfficialError as error:
            return self._result(self._code(error, "INVALID_INPUT"))
        except Exception:
            return self._result("STORAGE_ERROR")

    def cancel(self, scope, operation_id):
        """Authorized explicit CAS abort, including a crash before reserve dispatch."""
        operation, op_id = None, None
        try:
            scope, op_id = _scope(scope), uuid_value(operation_id)
            existing = self._visible_operation(scope, op_id, "cancel")
            with self._policy(scope, existing, "cancel"):
                operation = self.store.abort(environment_id=scope.environment_id, group_id=scope.group_id, operation_id=op_id)
            return self._settle(scope, operation)
        except OfficialError as error:
            return self._failure(scope, op_id, operation, error)
        except Exception:
            return self._result("STORAGE_ERROR", operation, operation_id=op_id)

    def unbind(self, scope, request):
        operation, op_id = None, None
        try:
            scope = _scope(scope)
            required = {"operation_id", "request_key", "actor_kind", "actor_id", "target_user_id",
                        "kind", "targets", "policy_revision", "authorization", "target_server_ids"}
            if not isinstance(request, dict) or set(request) != required:
                raise BindingError("INVALID_INPUT", "解绑请求字段不完整或无效。")
            op_id = uuid_value(request["operation_id"])
            with self._policy(scope, request, "submit") as lease:
                if lease.revision != request["policy_revision"]:
                    raise BindingError("CONFLICT", "当前策略已变化。")
                operation = self.store.prepare_unbind(**request, environment_id=scope.environment_id, group_id=scope.group_id)
                op_id = operation["operation_id"]
                operation = self.store.commit_unbind(environment_id=scope.environment_id, group_id=scope.group_id,
                                                     operation_id=op_id, policy_revision=lease.revision)
            return self._result(operation["result_code"], operation)
        except OfficialError as error:
            return self._failure(scope, op_id, operation, error)
        except Exception:
            return self._result("STORAGE_ERROR", operation, operation_id=op_id)
