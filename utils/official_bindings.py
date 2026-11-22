"""Transactional center operations for the QQ official binding tables.

The caller supplies a DB-API connection factory. This module never reads plugin
configuration or credentials and never performs network or Minecraft side effects.
"""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from .official_core import OfficialError, identifier, uuid_value


GAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
HEX_64 = re.compile(r"^[a-f0-9]{64}$")
MAX_REVISION = 9223372036854775807
MAX_TARGETS = 100
MAX_EFFECTS = 500
MAX_JSON_BYTES = 1024 * 1024
TERMINAL = {"COMMITTED", "REJECTED", "ABORTED"}
DELIVERY_PARTS = ("mirror", "whitelist", "online")
DELIVERY_STATES = {"PENDING", "APPLIED", "SUPERSEDED", "FAILED", "CONFLICT"}


class BindingError(OfficialError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _json(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        size = len(encoded.encode("utf-8"))
    except (ValueError, TypeError, RecursionError):
        raise BindingError("INVALID_INPUT", "操作字段格式无效。") from None
    if size > MAX_JSON_BYTES:
        raise BindingError("LIMIT_EXCEEDED", "操作记录超过容量限制。")
    return encoded


def _decode(value, default):
    if type(value) is type(default):
        _json(value)
        return value
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_JSON_BYTES:
        raise OfficialError("中心操作记录结构无效。")
    try:
        def unique(pairs):
            obj = {}
            for key, item in pairs:
                if key in obj:
                    raise ValueError
                obj[key] = item
            return obj
        result = json.loads(value, object_pairs_hook=unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (TypeError, ValueError, RecursionError):
        raise OfficialError("中心操作记录结构无效。") from None
    if not isinstance(result, type(default)):
        raise OfficialError("中心操作记录结构无效。")
    return result


def _revision(value):
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,18}", value):
        parsed = int(value)
    elif type(value) is int:
        parsed = value
    else:
        parsed = 0
    if not 1 <= parsed <= MAX_REVISION:
        raise OfficialError("策略修订号无效。")
    return parsed


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _delivery(servers, effects):
    return {server: {"mirror": "PENDING", "whitelist": "PENDING", "online": "PENDING",
        "effects": {effect["game_key"]: {"requested_revision": effect["revision"],
            "effective_revision": None, "mirror": "PENDING", "whitelist": "PENDING",
            "online": "PENDING"} for effect in effects}} for server in servers}


def _pending(followup):
    token = followup.get("token_finalize")
    reservation = followup.get("token_reservation")
    return bool((token and token["state"] == "PENDING") or
        (reservation and reservation["state"] == "PENDING") or any(
        target[part] not in ("APPLIED", "SUPERSEDED")
        for target in followup["delivery"].values() for part in ("mirror", "whitelist", "online")))


def _aggregate(target, part):
    values = {item[part] for item in target["effects"].values()}
    for status in ("CONFLICT", "FAILED", "PENDING", "SUPERSEDED"):
        if status in values:
            return status
    return "APPLIED"


class OfficialBindings:
    """Two-table transaction boundary for center-owned QQ official bindings.

    `connection_factory` must return a DB-API connection already configured by
    its owner. No default connection, configuration lookup, or local fallback is
    provided. Pass `placeholder='?'` for SQLite-only synthetic tests.
    """

    def __init__(self, connection_factory, *, placeholder="%s"):
        if not callable(connection_factory) or placeholder not in ("%s", "?"):
            raise ValueError("An explicit connection factory and supported placeholder are required.")
        self.connection_factory = connection_factory
        self.placeholder = placeholder
        self.supports_for_update = placeholder == "%s"

    @contextmanager
    def _transaction(self):
        connection = cursor = None
        committing = False
        try:
            connection = self.connection_factory()
            cursor = connection.cursor()
            begin = getattr(connection, "begin", None)
            if callable(begin):
                begin()
            else:
                cursor.execute("BEGIN IMMEDIATE" if not self.supports_for_update else "BEGIN")
            # The connection owner must disable transparent connector retries.
            guard = getattr(cursor, "no_failover", None)
            with guard() if callable(guard) else nullcontext():
                preflight = getattr(self, "_preflight", None)
                if callable(preflight):
                    preflight(cursor)
                yield connection, cursor
                committing = True
                connection.commit()
        except OfficialError:
            self._quiet(connection, "rollback")
            raise
        except Exception as error:
            self._quiet(connection, "rollback")
            if committing:
                raise BindingError("RESULT_UNKNOWN", "中心提交结果待确认；请查询同一操作编号，不要重新发起。") from None
            errno = error.args[0] if error.args else None
            if type(error).__name__ == "IntegrityError" or errno in (1062, 1213, 1205, 9007):
                raise BindingError("CONFLICT", "中心操作存在并发冲突；请查询同一操作后恢复。") from None
            if errno in (1054, 1146):
                raise BindingError("SCHEMA_MISMATCH", "官方中心表结构不匹配。") from None
            if errno in (1044, 1045, 1142, 1143):
                raise BindingError("UNAUTHORIZED", "官方中心访问权限不足。") from None
            raise BindingError("DB_UNAVAILABLE", "官方中心暂不可用；暂停变更，请查询原操作。") from None
        finally:
            self._quiet(cursor, "close")
            self._quiet(connection, "close")

    @staticmethod
    def _quiet(target, method):
        if target is not None:
            try:
                getattr(target, method)()
            except Exception:
                pass  # Cleanup cannot leak credentials or mask a committed result.

    def _sql(self, statement):
        return statement.replace("?", self.placeholder)

    def _one(self, cursor, statement, values=()):
        cursor.execute(self._sql(statement), values)
        row = cursor.fetchone()
        if row is None:
            return None
        if isinstance(row, dict):
            return row
        return {column[0]: row[i] for i, column in enumerate(cursor.description)}

    def _operation(self, cursor, operation_id, *, lock=True):
        suffix = " FOR UPDATE" if lock and self.supports_for_update else ""
        row = self._one(cursor,
            "SELECT operation_id, environment_id, group_id, request_key, payload_hash, kind, actor_kind, actor_id,"
            "target_user_id, original_server_id, token_generation, reservation_id, policy_revision, intent, nickname_basis,"
            "state, result_code, effects, followup, created_at, updated_at FROM bindings_qqofficial_ops"
            " WHERE operation_id=?" + suffix, (operation_id,))
        if row is not None:
            self._validate_operation(row)
        return row

    @staticmethod
    def _validate_operation(row):
        """Reject inconsistent persisted intent/results before any recovery write."""
        try:
            for field in ("operation_id", "environment_id"):
                uuid_value(row[field])
            for field in ("group_id", "actor_id", "target_user_id"):
                identifier(row[field])
            if (row["kind"] not in ("bind", "unbind_game", "unbind_user")
                    or row["state"] not in TERMINAL | {"PREPARED", "RESERVED"}
                    or not HEX_64.fullmatch(row["request_key"]) or not HEX_64.fullmatch(row["payload_hash"])):
                raise ValueError
            policy_revision = str(_revision(row["policy_revision"]))
            intent = _decode(row["intent"], {})
            basis = _decode(row["nickname_basis"], {}) if row["nickname_basis"] is not None else None
            auth = intent["authorization"]
            if (row["actor_kind"] not in ("qq_member", "game_admin") or not isinstance(auth, dict)
                    or set(auth) != {"scope", "policy_revision"}
                    or auth["scope"] not in ("self", "group", "environment")
                    or auth["policy_revision"] != policy_revision
                    or (auth["scope"] == "self" and (row["actor_kind"] != "qq_member"
                                                         or row["actor_id"] != row["target_user_id"]))):
                raise ValueError
            servers = intent["target_server_ids"]
            if not isinstance(servers, list) or len(servers) > MAX_TARGETS or servers != sorted(set(servers)):
                raise ValueError
            for server in servers:
                uuid_value(server)
            if row["kind"] == "bind":
                if set(intent) != {"authorization", "target_server_ids", "game_id", "game_key"}:
                    raise ValueError
                if not GAME_RE.fullmatch(intent["game_id"]) or intent["game_key"] != intent["game_id"].lower():
                    raise ValueError
                uuid_value(row["original_server_id"])
                uuid_value(row["token_generation"])
                if row["reservation_id"] is not None:
                    uuid_value(row["reservation_id"])
                if row["state"] in ("RESERVED", "COMMITTED") and row["reservation_id"] is None:
                    raise ValueError
                if basis is not None:
                    if (set(basis) != {"game_key", "expected_revision"} or not GAME_RE.fullmatch(basis["game_key"])
                            or basis["game_key"] != basis["game_key"].lower()
                            or basis["expected_revision"] != str(_revision(basis["expected_revision"]))):
                        raise ValueError
                keys = [intent["game_key"]]
            else:
                if (set(intent) != {"authorization", "target_server_ids", "targets"}
                        or any(row[field] is not None for field in ("original_server_id", "token_generation", "reservation_id"))
                        or basis is not None or row["state"] == "RESERVED"):
                    raise ValueError
                targets = intent["targets"]
                if not isinstance(targets, list) or not 1 <= len(targets) <= MAX_EFFECTS:
                    raise ValueError
                keys = []
                for target in targets:
                    if (set(target) != {"game_key", "expected_revision"} or not GAME_RE.fullmatch(target["game_key"])
                            or target["game_key"] != target["game_key"].lower()
                            or target["expected_revision"] != str(_revision(target["expected_revision"]))):
                        raise ValueError
                    keys.append(target["game_key"])
                if keys != sorted(set(keys)) or (row["kind"] == "unbind_game" and len(keys) != 1):
                    raise ValueError
            canonical = {"environment_id": row["environment_id"], "group_id": row["group_id"],
                "actor_kind": row["actor_kind"], "actor_id": row["actor_id"], "user_id": row["target_user_id"],
                "server_id": row["original_server_id"], "token_generation": row["token_generation"],
                "policy_revision": policy_revision, "kind": row["kind"], "intent": intent, "nickname_basis": basis}
            if payload_hash(canonical) != row["payload_hash"]:
                raise ValueError
            effects = _decode(row["effects"], [])
            followup = _decode(row["followup"], {})
            if not {"token_finalize", "delivery"} <= set(followup) <= {"token_finalize", "token_reservation", "delivery", "attempts"}:
                raise ValueError
            if "attempts" in followup:
                _revision(followup["attempts"])
            if row["state"] == "COMMITTED":
                if len(effects) != len(keys) or [e["game_key"] for e in effects] != keys:
                    raise ValueError
                for effect in effects:
                    if (set(effect) != {"game_key", "game_id", "user_id", "state", "revision"}
                            or not GAME_RE.fullmatch(effect["game_id"]) or effect["game_key"] != effect["game_id"].lower()
                            or effect["user_id"] != row["target_user_id"]
                            or effect["state"] != ("active" if row["kind"] == "bind" else "revoked")
                            or effect["revision"] != str(_revision(effect["revision"]))):
                        raise ValueError
                if set(followup["delivery"]) != set(servers):
                    raise ValueError
                for target in followup["delivery"].values():
                    if set(target) != {*DELIVERY_PARTS, "effects"} or set(target["effects"]) != set(keys):
                        raise ValueError
                    for effect in effects:
                        item = target["effects"][effect["game_key"]]
                        if (set(item) != {*DELIVERY_PARTS, "requested_revision", "effective_revision"}
                                or item["requested_revision"] != effect["revision"]
                                or any(item[part] not in DELIVERY_STATES for part in DELIVERY_PARTS)):
                            raise ValueError
                        if item["effective_revision"] is not None:
                            _revision(item["effective_revision"])
                    if any(target[part] != _aggregate(target, part) for part in DELIVERY_PARTS):
                        raise ValueError
            elif effects or followup["delivery"]:
                raise ValueError
            token = followup["token_finalize"]
            reservation = followup.get("token_reservation")
            if reservation is not None:
                if (row["kind"] != "bind" or row["state"] != "PREPARED"
                        or set(reservation) != {"state", "token_generation", "reservation_id"}
                        or reservation["state"] != "PENDING"
                        or reservation["token_generation"] != row["token_generation"]):
                    raise ValueError
                if reservation["reservation_id"] is not None:
                    uuid_value(reservation["reservation_id"])
            elif row["state"] == "PREPARED" and row["kind"] == "bind" and token is not None:
                raise ValueError
            if row["kind"] == "bind" and row["state"] in TERMINAL:
                decision = "COMMITTED" if row["state"] == "COMMITTED" else "ABORTED"
                done = "CONSUMED" if decision == "COMMITTED" else "INVALIDATED"
                if (not isinstance(token, dict) or set(token) != {"state", "decision", "reservation_id", "token_generation"}
                        or token["decision"] != decision or token["state"] not in ("PENDING", done)
                        or token["token_generation"] != row["token_generation"]
                        or (row["reservation_id"] is not None and token["reservation_id"] != row["reservation_id"])):
                    raise ValueError
                if token["reservation_id"] is not None:
                    uuid_value(token["reservation_id"])
                elif token["state"] != "PENDING":
                    raise ValueError
            elif token is not None:
                raise ValueError
        except (KeyError, ValueError, TypeError, AttributeError, RecursionError):
            raise BindingError("STORAGE_ERROR", "中心操作记录不一致，已暂停恢复；请核对存储。") from None

    def _lock_bindings(self, cursor, operation, keys):
        # Every mutation, including nickname-basis checks, uses the same order.
        locked = {key: self._one(cursor,
            "SELECT id,game_key,game_id,qq,state,revision FROM bindings_qqofficial"
            " WHERE environment_id=? AND group_id=? AND game_key=?"
            + (" FOR UPDATE" if self.supports_for_update else ""),
            (operation["environment_id"], operation["group_id"], key)) for key in sorted(set(keys))}
        for key, binding in locked.items():
            if binding is None:
                continue
            try:
                if (binding["game_key"] != key or not GAME_RE.fullmatch(binding["game_id"])
                        or binding["game_id"].lower() != key or binding["state"] not in ("active", "revoked")):
                    raise ValueError
                identifier(binding["qq"])
                _revision(binding["revision"])
            except (KeyError, TypeError, ValueError):
                raise BindingError("STORAGE_ERROR", "中心绑定行结构无效，已暂停变更。") from None
        return locked

    @staticmethod
    def _scoped(row, environment_id, group_id):
        if row is None or row["environment_id"] != environment_id or row["group_id"] != group_id:
            raise BindingError("SCOPE_MISMATCH", "未找到当前范围的操作。")
        return row

    @staticmethod
    def _public_operation(row):
        if row is None:
            return None
        value = dict(row)
        for field, default in (("intent", {}), ("nickname_basis", {}), ("effects", []), ("followup", {})):
            value[field] = _decode(value[field], default) if value[field] is not None else default
        value["policy_revision"] = str(value["policy_revision"])
        return value

    @staticmethod
    def _bind_payload(*, environment_id, group_id, actor_kind, actor_id, user_id, server_id, token_generation,
                      game_id, policy_revision, target_server_ids, authorization, nickname_basis):
        environment_id = uuid_value(environment_id)
        group_id = identifier(group_id)
        actor_id, user_id = identifier(actor_id), identifier(user_id)
        server_id, token_generation = uuid_value(server_id), uuid_value(token_generation)
        if actor_kind not in ("qq_member", "game_admin"):
            raise OfficialError("操作人类型无效。")
        if not isinstance(game_id, str) or not GAME_RE.fullmatch(game_id):
            raise OfficialError("游戏名无效。")
        if actor_kind == "qq_member" and actor_id != user_id:
            raise OfficialError("自助绑定的操作人和目标用户不一致。")
        if (not isinstance(authorization, dict) or set(authorization) != {"scope", "policy_revision"}
                or authorization["scope"] not in ("self", "group", "environment")
                or (actor_kind == "game_admin" and authorization["scope"] == "self")
                or _revision(authorization["policy_revision"]) != _revision(policy_revision)):
            raise OfficialError("操作授权声明无效。")
        policy_revision = _revision(policy_revision)
        if not isinstance(target_server_ids, list) or len(target_server_ids) > MAX_TARGETS:
            raise OfficialError("目标服务器数量超限。")
        targets = [uuid_value(value) for value in target_server_ids]
        if len(targets) != len(set(targets)):
            raise OfficialError("目标服务器不能重复。")
        targets.sort()
        basis = None
        if nickname_basis is not None:
            if not isinstance(nickname_basis, dict) or set(nickname_basis) != {"game_key", "expected_revision"}:
                raise OfficialError("昵称依据无效。")
            key = nickname_basis["game_key"]
            if not isinstance(key, str) or not GAME_RE.fullmatch(key) or key != key.lower():
                raise OfficialError("昵称依据无效。")
            basis = {"game_key": key, "expected_revision": str(_revision(nickname_basis["expected_revision"]))}
        intent = {
            "game_id": game_id, "game_key": game_id.lower(),
            "authorization": dict(authorization, policy_revision=str(policy_revision)), "target_server_ids": targets,
        }
        return {
            "environment_id": environment_id, "group_id": group_id, "actor_kind": actor_kind,
            "actor_id": actor_id, "user_id": user_id, "server_id": server_id,
            "token_generation": token_generation, "policy_revision": str(policy_revision),
            "kind": "bind", "intent": intent, "nickname_basis": basis,
        }

    def prepare_bind(self, *, operation_id, request_key, environment_id, group_id, actor_kind, actor_id,
                     user_id, server_id, token_generation, game_id, policy_revision, target_server_ids,
                     authorization, nickname_basis=None):
        operation_id = uuid_value(operation_id)
        if not isinstance(request_key, str) or not HEX_64.fullmatch(request_key):
            raise OfficialError("请求幂等键无效。")
        intent = self._bind_payload(environment_id=environment_id, group_id=group_id, actor_kind=actor_kind,
            actor_id=actor_id, user_id=user_id, server_id=server_id, token_generation=token_generation,
            game_id=game_id, policy_revision=policy_revision, target_server_ids=target_server_ids,
            authorization=authorization, nickname_basis=nickname_basis)
        return self._prepare(operation_id, request_key, intent)

    def _prepare(self, operation_id, request_key, intent):
        operation_id = uuid_value(operation_id)
        if not isinstance(request_key, str) or not HEX_64.fullmatch(request_key):
            raise BindingError("INVALID_INPUT", "请求幂等键无效。")
        # Reserve room for the full outbox BEFORE creating PREPARED/reserving a
        # code. Revision strings and later ACKs use their maximum encoded width.
        keys = ([intent["intent"]["game_key"]] if intent["kind"] == "bind"
                else [target["game_key"] for target in intent["intent"]["targets"]])
        effects = [{"game_key": key, "game_id": "X" * 16, "user_id": intent["user_id"],
                    "state": "revoked", "revision": str(MAX_REVISION)} for key in keys]
        delivery = _delivery(intent["intent"]["target_server_ids"], effects)
        for target in delivery.values():
            target.update(mirror="SUPERSEDED", whitelist="SUPERSEDED", online="SUPERSEDED")
            for item in target["effects"].values():
                item.update(effective_revision=str(MAX_REVISION), mirror="SUPERSEDED",
                            whitelist="SUPERSEDED", online="SUPERSEDED")
        _json({"effects": effects, "followup": {"delivery": delivery, "attempts": str(MAX_REVISION),
               "token_finalize": {"reservation_id": "0" * 36, "token_generation": "0" * 36,
                                  "state": "INVALIDATED", "decision": "COMMITTED"}},
               "nickname_basis": intent["nickname_basis"]})
        digest = payload_hash(intent)
        now = _now()
        with self._transaction() as (_connection, cursor):
            same_id = self._operation(cursor, operation_id)
            if same_id is not None:
                if same_id["payload_hash"] != digest or same_id["request_key"] != request_key:
                    raise BindingError("CONFLICT", "该操作编号已用于不同载荷。")
                return self._public_operation(same_id)
            existing = self._one(cursor,
                "SELECT operation_id, environment_id, request_key, payload_hash FROM bindings_qqofficial_ops"
                " WHERE environment_id=? AND request_key=?" + (" FOR UPDATE" if self.supports_for_update else ""),
                (intent["environment_id"], request_key))
            if existing is not None:
                if existing["payload_hash"] != digest:
                    raise BindingError("CONFLICT", "请求幂等键已用于不同操作，请查询原操作。")
                return self._public_operation(self._operation(cursor, existing["operation_id"], lock=False))
            if intent["token_generation"] is not None:
                generation = self._one(cursor,
                    "SELECT operation_id,payload_hash,state FROM bindings_qqofficial_ops"
                    " WHERE environment_id=? AND original_server_id=? AND token_generation=?"
                    + (" FOR UPDATE" if self.supports_for_update else ""),
                    (intent["environment_id"], intent["server_id"], intent["token_generation"]))
                if generation is not None:
                    if generation["payload_hash"] != digest or generation["state"] in ("ABORTED", "REJECTED"):
                        raise BindingError("CONFLICT", "验证码代次已被占用或作废，请查询原操作或取得新码。")
                    return self._public_operation(self._operation(cursor, generation["operation_id"], lock=False))
            p = self.placeholder
            cursor.execute(
                "INSERT INTO bindings_qqofficial_ops (operation_id,environment_id,group_id,request_key,payload_hash,kind,"
                "actor_kind,actor_id,target_user_id,original_server_id,token_generation,reservation_id,policy_revision,intent,"
                "nickname_basis,state,result_code,effects,followup,next_attempt_at,created_at,updated_at)"
                f" VALUES ({','.join([p] * 22)})",
                (operation_id, intent["environment_id"], intent["group_id"], request_key, digest, intent["kind"],
                 intent["actor_kind"], intent["actor_id"], intent["user_id"], intent["server_id"],
                 intent["token_generation"], None, int(intent["policy_revision"]), _json(intent["intent"]),
                 _json(intent["nickname_basis"]) if intent["nickname_basis"] else None,
                 "PREPARED", "PREPARED", "[]", _json({"token_finalize": None, "delivery": {}}),
                 None, now, now))
            return self._public_operation(self._operation(cursor, operation_id, lock=False))

    def get_operation(self, environment_id, operation_id, *, group_id):
        environment_id, operation_id = uuid_value(environment_id), uuid_value(operation_id)
        identifier(group_id)
        with self._transaction() as (_connection, cursor):
            row = self._operation(cursor, operation_id, lock=False)
            if row is None or row["environment_id"] != environment_id or row["group_id"] != group_id:
                return None
            return self._public_operation(row)

    def find_request(self, environment_id, group_id, request_key):
        uuid_value(environment_id)
        identifier(group_id)
        if not isinstance(request_key, str) or not HEX_64.fullmatch(request_key):
            raise BindingError("INVALID_INPUT", "请求幂等键无效。")
        with self._transaction() as (_connection, cursor):
            row = self._one(cursor, "SELECT operation_id FROM bindings_qqofficial_ops"
                " WHERE environment_id=? AND group_id=? AND request_key=?",
                (environment_id, group_id, request_key))
            return self._public_operation(self._operation(cursor, row["operation_id"], lock=False)) if row else None

    def resolve_operation(self, environment_id, group_id, prefix, *, requester=None, administrator=False):
        """Resolve a bounded prefix only among this caller's visible operations."""
        uuid_value(environment_id)
        identifier(group_id)
        if not administrator or requester is not None:
            identifier(requester)
        if (not isinstance(prefix, str) or not re.fullmatch(r"[0-9a-f-]{8,36}", prefix)
                or type(administrator) is not bool):
            raise BindingError("INVALID_INPUT", "操作编号格式无效。")
        with self._transaction() as (_connection, cursor):
            sql = ("SELECT operation_id FROM bindings_qqofficial_ops WHERE environment_id=?"
                   " AND group_id=? AND operation_id LIKE ?")
            values = [environment_id, group_id, prefix + "%"]
            if not administrator:
                sql += " AND ((actor_kind='qq_member' AND actor_id=?) OR target_user_id=?)"
                values.extend((requester, requester))
            cursor.execute(self._sql(sql + " ORDER BY operation_id LIMIT 2"), tuple(values))
            rows = cursor.fetchall()
            if not rows:
                raise BindingError("SCOPE_MISMATCH", "未找到当前身份可查看的操作。")
            if len(rows) != 1:
                raise BindingError("CONFLICT", "操作编号前缀不唯一，请输入更完整编号。")
            operation_id = rows[0]["operation_id"] if isinstance(rows[0], dict) else rows[0][0]
            return self._public_operation(self._operation(cursor, operation_id, lock=False))

    def operation_summaries(self, environment_id, group_id, *, pending=True, limit=201):
        """Bounded administrator metadata; no identities, intent or credential data."""
        uuid_value(environment_id)
        identifier(group_id)
        if type(pending) is not bool or type(limit) is not int or not 1 <= limit <= 201:
            raise BindingError("INVALID_INPUT", "操作查询范围无效。")
        with self._transaction() as (_connection, cursor):
            sql = ("SELECT operation_id,kind,state,result_code,policy_revision,created_at,updated_at"
                   " FROM bindings_qqofficial_ops WHERE environment_id=? AND group_id=?")
            if pending:
                sql += " AND (state IN ('PREPARED','RESERVED') OR next_attempt_at IS NOT NULL)"
            cursor.execute(self._sql(sql + " ORDER BY created_at DESC,operation_id DESC LIMIT ?"),
                           (environment_id, group_id, limit))
            result = []
            for row in cursor.fetchall():
                item = row if isinstance(row, dict) else dict(zip((col[0] for col in cursor.description), row))
                uuid_value(item["operation_id"])
                if (item["kind"] not in ("bind", "unbind_game", "unbind_user")
                        or item["state"] not in TERMINAL | {"PREPARED", "RESERVED"}
                        or item["result_code"] is not None and (not isinstance(item["result_code"], str)
                            or not re.fullmatch(r"[A-Z_]{1,32}", item["result_code"]))):
                    raise BindingError("STORAGE_ERROR", "操作摘要结构无效。")
                item = dict(item, policy_revision=str(_revision(item["policy_revision"])))
                for key in ("created_at", "updated_at"):
                    try:
                        value = item[key]
                        if isinstance(value, str):
                            value = datetime.fromisoformat(value)
                        if not isinstance(value, datetime) or value.utcoffset() not in (None, timedelta(0)):
                            raise ValueError
                        item[key] = value.replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"
                    except (ValueError, TypeError):
                        raise BindingError("STORAGE_ERROR", "操作时间字段无效。") from None
                result.append(item)
            return result

    def mark_reservation_pending(self, *, environment_id, group_id, operation_id, token_generation):
        operation, _claimed = self.claim_reservation(environment_id=environment_id, group_id=group_id,
            operation_id=operation_id, token_generation=token_generation)
        return operation

    def claim_reservation(self, *, environment_id, group_id, operation_id, token_generation):
        """Persist dispatch intent BEFORE sending; only its first claimant may reserve."""
        environment_id, operation_id, token_generation = uuid_value(environment_id), uuid_value(operation_id), uuid_value(token_generation)
        identifier(group_id)
        with self._transaction() as (_connection, cursor):
            row = self._scoped(self._operation(cursor, operation_id), environment_id, group_id)
            if row["kind"] != "bind" or row["state"] != "PREPARED" or row["token_generation"] != token_generation:
                raise BindingError("CONFLICT", "该操作不能进入验证码恢复状态。")
            followup = _decode(row["followup"], {})
            if followup.get("token_reservation") is not None:
                return self._public_operation(row), False
            followup["token_reservation"] = {"state": "PENDING", "token_generation": token_generation,
                                                  "reservation_id": row["reservation_id"]}
            now = _now()
            cursor.execute(self._sql(
                "UPDATE bindings_qqofficial_ops SET followup=?,next_attempt_at=?,updated_at=? WHERE operation_id=? AND state='PREPARED'"),
                (_json(followup), now, now, operation_id))
            if cursor.rowcount != 1:
                raise BindingError("CONFLICT", "操作状态已变化，请查询原操作。")
            return self._public_operation(self._operation(cursor, operation_id, lock=False)), True

    def check(self, environment_id, group_id, *, game_id=None, user_id=None, limit=100, cursor=None):
        environment_id, group_id = uuid_value(environment_id), identifier(group_id)
        if (game_id is None) == (user_id is None):
            raise BindingError("INVALID_INPUT", "game_id 与 user_id 必须二选一。")
        if game_id is not None and (not isinstance(game_id, str) or not GAME_RE.fullmatch(game_id)):
            raise BindingError("INVALID_INPUT", "游戏名无效。")
        if user_id is not None:
            user_id = identifier(user_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise BindingError("INVALID_INPUT", "查询数量必须为 1–100。")
        if cursor is not None and (not isinstance(cursor, str) or not GAME_RE.fullmatch(cursor)):
            raise BindingError("INVALID_INPUT", "分页游标无效。")
        with self._transaction() as (_connection, cursor_obj):
            where = ["environment_id=?", "group_id=?", "state='active'"]
            values = [environment_id, group_id]
            if game_id is not None:
                where.append("game_key=?")
                values.append(game_id.lower())
            else:
                where.append("qq=?")
                values.append(user_id)
            if cursor is not None:
                where.append("game_key>?")
                values.append(cursor)
            sql = "SELECT game_id,game_key,qq,state,revision FROM bindings_qqofficial WHERE " + " AND ".join(where)
            sql += " ORDER BY game_key LIMIT ?"
            values.append(limit + 1)
            cursor_obj.execute(self._sql(sql), tuple(values))
            rows = cursor_obj.fetchall()
            result = []
            for row in rows[:limit]:
                if isinstance(row, dict):
                    item = row
                else:
                    item = {column[0]: row[i] for i, column in enumerate(cursor_obj.description)}
                identifier(item["game_id"])
                if not GAME_RE.fullmatch(item["game_key"]):
                    raise BindingError("STORAGE_ERROR", "中心绑定行结构无效。")
                _revision(item["revision"])
                result.append(item)
            next_cursor = result[-1]["game_key"] if len(rows) > limit else None
            return result, next_cursor

    @staticmethod
    def _binding_row(row):
        try:
            if (not isinstance(row["game_id"], str) or not GAME_RE.fullmatch(row["game_id"])
                    or row["game_key"] != row["game_id"].lower()
                    or row["state"] not in ("active", "revoked")):
                raise ValueError
            identifier(row["qq"])
            revision = str(_revision(row["revision"]))
            return dict(game_id=row["game_id"], game_key=row["game_key"],
                        state=row["state"], revision=revision)
        except (KeyError, TypeError, ValueError):
            raise BindingError("STORAGE_ERROR", "中心绑定行结构无效。") from None

    def check_game(self, environment_id, group_id, game_id):
        uuid_value(environment_id)
        identifier(group_id)
        if not isinstance(game_id, str) or not GAME_RE.fullmatch(game_id):
            raise BindingError("INVALID_INPUT", "游戏名无效。")
        with self._transaction() as (_connection, cursor):
            row = self._one(cursor, "SELECT game_id,game_key,qq,state,revision FROM bindings_qqofficial"
                " WHERE environment_id=? AND group_id=? AND game_key=?",
                (environment_id, group_id, game_id.lower()))
            if row is None:
                return {"game_id": game_id, "bound": False, "revision": None}
            result = self._binding_row(row)
            result["bound"] = result["state"] == "active"
            return result

    def active_snapshot(self, environment_id, group_id, user_id, *, max_rows=4096):
        """One bounded consistent SELECT; the protocol owns snapshot pagination."""
        uuid_value(environment_id)
        identifier(group_id)
        identifier(user_id)
        if type(max_rows) is not int or not 1 <= max_rows <= 4096:
            raise BindingError("INVALID_INPUT", "快照容量无效。")
        with self._transaction() as (_connection, cursor):
            cursor.execute(self._sql(
                "SELECT game_id,game_key,qq,state,revision FROM bindings_qqofficial"
                " WHERE environment_id=? AND group_id=? AND qq=? AND state='active' ORDER BY game_key LIMIT ?"),
                (environment_id, group_id, user_id, max_rows + 1))
            rows = cursor.fetchall()
            if len(rows) > max_rows:
                raise BindingError("LIMIT_EXCEEDED", "绑定查询超出快照容量，请缩小范围。")
            result = []
            for row in rows:
                record = row if isinstance(row, dict) else dict(zip((col[0] for col in cursor.description), row))
                if record["qq"] != user_id:
                    raise BindingError("STORAGE_ERROR", "中心身份比较规则不匹配。")
                result.append(self._binding_row(record))
            _json(result)
            return result

    def mirror_snapshot(self, environment_id, group_id, *, max_rows=10000):
        """Internal center snapshot, including tombstones, from ONE transaction.

        Every row must be present in its last COMMITTED operation's fixed effects.
        No HTTP publication, authority permit, old-table fallback or partial result.
        """
        uuid_value(environment_id)
        identifier(group_id)
        if type(max_rows) is not int or not 1 <= max_rows <= 10000:
            raise BindingError("INVALID_INPUT", "镜像快照容量无效。")
        with self._transaction() as (_connection, cursor):
            cursor.execute(self._sql(
                "SELECT environment_id,group_id,game_id,game_key,qq,state,revision,last_operation_id"
                " FROM bindings_qqofficial WHERE environment_id=? AND group_id=? ORDER BY game_key LIMIT ?"),
                (environment_id, group_id, max_rows + 1))
            records = cursor.fetchmany(max_rows + 1)
            if len(records) > max_rows:
                raise BindingError("LIMIT_EXCEEDED", "中心镜像快照超出容量，未应用部分结果。")
            result, references, previous, size = [], {}, "", 2
            for value in records:
                row = value if isinstance(value, dict) else dict(zip((field[0] for field in cursor.description), value))
                try:
                    if row["environment_id"] != environment_id or row["group_id"] != group_id:
                        raise ValueError
                    effect = dict(self._binding_row(row), user_id=identifier(row["qq"]))
                    operation = uuid_value(row["last_operation_id"])
                    key = effect["game_key"]
                    if key <= previous:
                        raise ValueError
                    previous = key
                    item = dict(effect, last_operation_id=operation)
                    size += len(_json(item).encode("utf-8")) + 1
                    if size > 8 * 1024 * 1024:
                        raise BindingError("LIMIT_EXCEEDED", "中心镜像快照超过字节上限。")
                    result.append(item)
                    references.setdefault(operation, {})[key] = effect
                except BindingError:
                    raise
                except (KeyError, TypeError, ValueError):
                    raise BindingError("STORAGE_ERROR", "中心镜像行不符合当前来源或结构。") from None
            for operation, expected in sorted(references.items()):
                record = self._operation(cursor, operation, lock=False)
                if (record is None or record["environment_id"] != environment_id or record["group_id"] != group_id
                        or record["state"] != "COMMITTED"):
                    raise BindingError("STORAGE_ERROR", "中心镜像缺少匹配的已提交操作。")
                effects = {effect["game_key"]: effect for effect in _decode(record["effects"], [])}
                if any(effects.get(key) != effect for key, effect in expected.items()):
                    raise BindingError("STORAGE_ERROR", "中心镜像与固定提交结果不一致。")
            return result

    def reserve(self, *, environment_id, group_id, operation_id, reservation_id, token_generation):
        environment_id, operation_id = uuid_value(environment_id), uuid_value(operation_id)
        identifier(group_id)
        reservation_id, token_generation = uuid_value(reservation_id), uuid_value(token_generation)
        now = _now()
        with self._transaction() as (_connection, cursor):
            row = self._scoped(self._operation(cursor, operation_id), environment_id, group_id)
            if row["token_generation"] != token_generation:
                raise OfficialError("绑定码代次与操作不一致。")
            if row["state"] in ("RESERVED", "COMMITTED"):
                if row["reservation_id"] != reservation_id:
                    raise OfficialError("该绑定操作已经预占了另一份验证码。")
                return self._public_operation(row)
            if row["state"] != "PREPARED":
                raise OfficialError("该操作当前状态不能预占验证码。")
            cursor.execute(self._sql(
                "UPDATE bindings_qqofficial_ops SET reservation_id=?,state='RESERVED',result_code='RESERVED',followup=?,next_attempt_at=?,updated_at=?"
                " WHERE operation_id=? AND environment_id=? AND state='PREPARED' AND token_generation=?"),
                (reservation_id, _json({"token_finalize": None, "delivery": {}}), now, now, operation_id, environment_id, token_generation))
            if cursor.rowcount != 1:
                raise OfficialError("绑定操作状态已变化，请查询原操作。")
            return self._public_operation(self._operation(cursor, operation_id, lock=False))

    def commit_bind(self, *, environment_id, group_id, operation_id, policy_revision):
        environment_id, operation_id = uuid_value(environment_id), uuid_value(operation_id)
        identifier(group_id)
        policy_revision = _revision(policy_revision)
        now = _now()
        with self._transaction() as (_connection, cursor):
            row = self._scoped(self._operation(cursor, operation_id), environment_id, group_id)
            if row["state"] in TERMINAL:
                return self._public_operation(row)
            if row["state"] != "RESERVED" or not row["reservation_id"]:
                raise OfficialError("验证码尚未持久预占，不能提交绑定。")
            if row["kind"] != "bind":
                raise BindingError("CONFLICT", "操作种类不匹配。")
            if int(row["policy_revision"]) != policy_revision:
                return self._reject(cursor, row, "CONFLICT", now)
            intent = _decode(row["intent"], {})
            if set(intent) != {"game_id", "game_key", "authorization", "target_server_ids"}:
                raise OfficialError("绑定操作意图无效。")
            game_id, game_key = intent["game_id"], intent["game_key"]
            user_id = row["target_user_id"]
            nickname_basis = _decode(row["nickname_basis"], {}) if row["nickname_basis"] else None
            keys = {game_key}
            if nickname_basis:
                keys.add(nickname_basis["game_key"])
            locked = self._lock_bindings(cursor, row, keys)
            if nickname_basis:
                basis = locked[nickname_basis["game_key"]]
                if (basis is None or basis["state"] != "active" or basis["qq"] != user_id
                        or int(basis["revision"]) != int(nickname_basis["expected_revision"])):
                    return self._reject(cursor, row, "CONFLICT", now)
            binding = locked[game_key]
            if binding is not None and binding["state"] == "active":
                code = "ALREADY_BOUND" if binding["qq"] == user_id else "CONFLICT"
                return self._reject(cursor, row, code, now)
            revision = int(binding["revision"]) + 1 if binding is not None else 1
            if revision > MAX_REVISION:
                return self._reject(cursor, row, "LIMIT_EXCEEDED", now)
            if binding is None:
                p = self.placeholder
                cursor.execute(
                    "INSERT INTO bindings_qqofficial (environment_id,group_id,game_key,game_id,qq,state,revision,"
                    "last_operation_id,bind_time,updated_at) VALUES (" + ",".join([p] * 10) + ")",
                    (environment_id, row["group_id"], game_key, game_id, user_id, "active", revision,
                     operation_id, now, now))
            else:
                cursor.execute(self._sql(
                    "UPDATE bindings_qqofficial SET game_id=?,qq=?,state='active',revision=?,last_operation_id=?,"
                    "bind_time=?,updated_at=? WHERE id=? AND state='revoked' AND revision=?"),
                    (game_id, user_id, revision, operation_id, now, now, binding["id"], binding["revision"]))
                if cursor.rowcount != 1:
                    raise OfficialError("绑定行版本已变化，请查询后重试。")
            effect = {"game_key": game_key, "game_id": game_id, "user_id": user_id,
                      "state": "active", "revision": str(revision)}
            targets = intent["target_server_ids"]
            delivery = _delivery(targets, [effect])
            followup = {"token_finalize": {"state": "PENDING", "decision": "COMMITTED",
                                               "reservation_id": row["reservation_id"],
                                               "token_generation": row["token_generation"]},
                        "delivery": delivery}
            cursor.execute(self._sql(
                "UPDATE bindings_qqofficial_ops SET state='COMMITTED',result_code='SYNC_PENDING',effects=?,followup=?,"
                "next_attempt_at=?,updated_at=? WHERE operation_id=? AND environment_id=? AND state='RESERVED'"),
                (_json([effect]), _json(followup), now, now, operation_id, environment_id))
            if cursor.rowcount != 1:
                raise OfficialError("绑定操作状态已变化，请查询原操作。")
            return self._public_operation(self._operation(cursor, operation_id, lock=False))

    def _reject(self, cursor, row, code, now, *, state="REJECTED"):
        followup = _decode(row["followup"], {})
        followup.pop("token_reservation", None)
        if row["token_generation"]:
            # PREPARED may already have a remote reservation whose ACK was lost.
            # Recovery must query the original server even without reservation_id.
            followup["token_finalize"] = {"state": "PENDING", "decision": "ABORTED",
                                            "reservation_id": row["reservation_id"],
                                            "token_generation": row["token_generation"]}
        cursor.execute(self._sql(
            "UPDATE bindings_qqofficial_ops SET state=?,result_code=?,followup=?,next_attempt_at=?,updated_at=?"
            " WHERE operation_id=? AND state=?"),
            (state, code, _json(followup), now if _pending(followup) else None, now, row["operation_id"], row["state"]))
        if cursor.rowcount != 1:
            raise OfficialError("绑定操作状态已变化，请查询原操作。")
        return self._public_operation(self._operation(cursor, row["operation_id"], lock=False))

    def abort(self, *, environment_id, group_id, operation_id, result_code="OK"):
        environment_id, operation_id = uuid_value(environment_id), uuid_value(operation_id)
        identifier(group_id)
        if result_code not in ("OK", "UNAUTHORIZED", "CONFLICT"):
            raise BindingError("INVALID_INPUT", "中止原因无效。")
        with self._transaction() as (_connection, cursor):
            row = self._scoped(self._operation(cursor, operation_id), environment_id, group_id)
            if row["state"] in TERMINAL:
                return self._public_operation(row)
            if row["state"] not in ("PREPARED", "RESERVED"):
                raise BindingError("CONFLICT", "操作状态无效。")
            return self._reject(cursor, row, result_code, _now(), state="ABORTED")

    def prepare_unbind(self, *, operation_id, request_key, environment_id, group_id, actor_kind, actor_id,
                       target_user_id, kind, targets, policy_revision, authorization, target_server_ids):
        environment_id, group_id = uuid_value(environment_id), identifier(group_id)
        actor_id, user_id = identifier(actor_id), identifier(target_user_id)
        revision = str(_revision(policy_revision))
        if (actor_kind not in ("qq_member", "game_admin") or not isinstance(authorization, dict)
                or set(authorization) != {"scope", "policy_revision"}
                or authorization["scope"] not in ("self", "group", "environment")
                or str(_revision(authorization["policy_revision"])) != revision
                or (authorization["scope"] == "self" and (actor_kind != "qq_member" or actor_id != user_id))):
            raise BindingError("UNAUTHORIZED", "操作授权声明与目标不一致。")
        if (kind not in ("unbind_game", "unbind_user") or not isinstance(targets, list)
                or not 1 <= len(targets) <= MAX_EFFECTS or (kind == "unbind_game" and len(targets) != 1)):
            raise BindingError("INVALID_INPUT", "解绑目标数量或类型无效。")
        fixed = []
        for target in targets:
            if not isinstance(target, dict) or set(target) != {"game_key", "expected_revision"}:
                raise BindingError("INVALID_INPUT", "解绑目标字段无效。")
            key = target["game_key"]
            if not isinstance(key, str) or not GAME_RE.fullmatch(key) or key != key.lower():
                raise BindingError("INVALID_INPUT", "解绑游戏名无效。")
            fixed.append({"game_key": key, "expected_revision": str(_revision(target["expected_revision"]))})
        if len({v["game_key"] for v in fixed}) != len(fixed):
            raise BindingError("INVALID_INPUT", "解绑目标不能重复。")
        if not isinstance(target_server_ids, list) or len(target_server_ids) > MAX_TARGETS:
            raise BindingError("LIMIT_EXCEEDED", "目标服务器数量超限。")
        servers = [uuid_value(v) for v in target_server_ids]
        if len(servers) != len(set(servers)):
            raise BindingError("INVALID_INPUT", "目标服务器不能重复。")
        intent = {"targets": sorted(fixed, key=lambda v: v["game_key"]),
                  "authorization": dict(authorization, policy_revision=revision), "target_server_ids": sorted(servers)}
        return self._prepare(operation_id, request_key, {
            "environment_id": environment_id, "group_id": group_id, "actor_kind": actor_kind,
            "actor_id": actor_id, "user_id": user_id, "server_id": None, "token_generation": None,
            "policy_revision": revision, "kind": kind, "intent": intent, "nickname_basis": None})

    def commit_unbind(self, *, environment_id, group_id, operation_id, policy_revision):
        environment_id, operation_id = uuid_value(environment_id), uuid_value(operation_id)
        identifier(group_id)
        policy_revision = _revision(policy_revision)
        with self._transaction() as (_connection, cursor):
            row = self._scoped(self._operation(cursor, operation_id), environment_id, group_id)
            if row["state"] in TERMINAL:
                return self._public_operation(row)
            if row["state"] != "PREPARED" or row["kind"] not in ("unbind_game", "unbind_user"):
                raise BindingError("CONFLICT", "操作状态或种类不匹配。")
            now = _now()
            if int(row["policy_revision"]) != policy_revision:
                return self._reject(cursor, row, "CONFLICT", now)
            intent = _decode(row["intent"], {})
            targets = intent["targets"]
            locked = self._lock_bindings(cursor, row, (v["game_key"] for v in targets))
            # Validate the entire fixed set before writing the first row.
            for target in targets:
                binding = locked[target["game_key"]]
                if (binding is None or binding["state"] != "active" or binding["qq"] != row["target_user_id"]
                        or int(binding["revision"]) != int(target["expected_revision"])):
                    return self._reject(cursor, row, "CONFLICT", now)
                if int(binding["revision"]) >= MAX_REVISION:
                    return self._reject(cursor, row, "LIMIT_EXCEEDED", now)
            effects = []
            for target in targets:
                binding = locked[target["game_key"]]
                next_revision = int(binding["revision"]) + 1
                cursor.execute(self._sql(
                    "UPDATE bindings_qqofficial SET state='revoked',revision=?,last_operation_id=?,updated_at=?"
                    " WHERE id=? AND environment_id=? AND group_id=? AND qq=? AND state='active' AND revision=?"),
                    (next_revision, operation_id, now, binding["id"], environment_id, group_id,
                     row["target_user_id"], binding["revision"]))
                if cursor.rowcount != 1:
                    raise BindingError("CONFLICT", "绑定版本已变化，本次解绑未提交。")
                effects.append({"game_key": binding["game_key"], "game_id": binding["game_id"],
                                "user_id": binding["qq"], "state": "revoked", "revision": str(next_revision)})
            followup = {"token_finalize": None, "delivery": _delivery(intent["target_server_ids"], effects)}
            cursor.execute(self._sql(
                "UPDATE bindings_qqofficial_ops SET state='COMMITTED',result_code=?,effects=?,followup=?,next_attempt_at=?,updated_at=?"
                " WHERE operation_id=? AND state='PREPARED'"),
                ("SYNC_PENDING" if followup["delivery"] else "OK", _json(effects), _json(followup),
                 now if _pending(followup) else None, now, operation_id))
            if cursor.rowcount != 1:
                raise BindingError("CONFLICT", "操作状态已变化，本次解绑未提交。")
            return self._public_operation(self._operation(cursor, operation_id, lock=False))

    def acknowledge(self, *, environment_id, group_id, operation_id, server_id,
                    token_state=None, reservation_id=None, token_generation=None, results=None):
        """Merge a verified V2 response; never accept an arbitrary JSON overwrite.

        Callers authenticate the responding server and recheck the exact scope.
        Results follow the contract's sync.results objects, including revisions.
        """
        environment_id, operation_id, server_id = map(uuid_value, (environment_id, operation_id, server_id))
        identifier(group_id)
        with self._transaction() as (_connection, cursor):
            row = self._scoped(self._operation(cursor, operation_id), environment_id, group_id)
            if row["state"] not in TERMINAL:
                raise BindingError("CONFLICT", "业务尚未确定，不能确认副作用。")
            followup = _decode(row["followup"], {})
            if token_state is not None:
                token = followup.get("token_finalize")
                expected = "CONSUMED" if row["state"] == "COMMITTED" else "INVALIDATED"
                if (not token or server_id != row["original_server_id"] or token_state != expected
                        or token_generation != row["token_generation"]
                        or (token["reservation_id"] is not None and reservation_id != token["reservation_id"])):
                    raise BindingError("CONFLICT", "验证码确认与原操作不一致。")
                uuid_value(reservation_id)
                token.update(state=token_state, reservation_id=reservation_id)
            if results is not None:
                if (row["state"] != "COMMITTED" or server_id not in followup["delivery"]
                        or not isinstance(results, list) or not 1 <= len(results) <= MAX_EFFECTS):
                    raise BindingError("SCOPE_MISMATCH", "同步确认不属于该操作目标。")
                target = followup["delivery"][server_id]
                seen = set()
                for result in results:
                    fields = {"game_key", "result", "requested_revision", "effective_revision",
                              "persisted", "whitelist", "online"}
                    if not isinstance(result, dict) or set(result) != fields:
                        raise BindingError("INVALID_INPUT", "同步确认字段无效。")
                    key = result["game_key"]
                    if not isinstance(key, str) or key not in target["effects"] or key in seen:
                        raise BindingError("CONFLICT", "同步确认账号不属于操作或重复。")
                    seen.add(key)
                    item = target["effects"][key]
                    requested, effective = _revision(result["requested_revision"]), _revision(result["effective_revision"])
                    if (str(requested) != item["requested_revision"] or type(result["persisted"]) is not bool
                            or result["result"] not in ("applied", "ignored", "conflict")):
                        raise BindingError("CONFLICT", "同步确认版本不匹配。")
                    states = {"PENDING", "APPLIED", "SUPERSEDED", "FAILED", "CONFLICT"}
                    if result["whitelist"] not in states or result["online"] not in states:
                        raise BindingError("INVALID_INPUT", "副作用确认无效。")
                    if result["result"] == "conflict":
                        mirror = "CONFLICT"
                    elif not result["persisted"]:
                        mirror = "PENDING"
                    elif effective < requested:
                        raise BindingError("CONFLICT", "确认版本早于请求版本。")
                    elif effective == requested:
                        mirror = "APPLIED"
                    else:
                        mirror = "SUPERSEDED"
                    if (result["result"] == "applied" and effective != requested
                            or any(result[part] == "SUPERSEDED" and mirror != "SUPERSEDED" for part in ("whitelist", "online"))
                            or any(result[part] == "APPLIED" and mirror != "APPLIED" for part in ("whitelist", "online"))):
                        raise BindingError("CONFLICT", "副作用确认与持久版本不一致。")
                    previous = int(item["effective_revision"] or 0)
                    if effective < previous:
                        continue  # A delayed ACK cannot regress an already observed version.
                    if item["mirror"] == "CONFLICT":
                        continue  # Conflicts require explicit reconciliation, not a later automatic overwrite.
                    if result["persisted"] or mirror == "CONFLICT":
                        item["effective_revision"] = str(effective)
                    for part, status in (("mirror", mirror), ("whitelist", result["whitelist"]), ("online", result["online"])):
                        if item[part] in ("APPLIED", "SUPERSEDED") and status in ("PENDING", "FAILED"):
                            continue
                        item[part] = status
                for part in ("mirror", "whitelist", "online"):
                    target[part] = _aggregate(target, part)
            if token_state is None and results is None:
                raise BindingError("INVALID_INPUT", "缺少确认结果。")
            code = ("SYNC_PENDING" if _pending(followup) else "OK") if row["state"] == "COMMITTED" else row["result_code"]
            now = _now()
            cursor.execute(self._sql(
                "UPDATE bindings_qqofficial_ops SET followup=?,result_code=?,next_attempt_at=?,updated_at=? WHERE operation_id=?"),
                (_json(followup), code, now if _pending(followup) else None, now, operation_id))
            return self._public_operation(self._operation(cursor, operation_id, lock=False))

    def due_operations(self, environment_id, *, limit=100):
        """Internal worker input, never a public operation-list endpoint."""
        uuid_value(environment_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise BindingError("INVALID_INPUT", "待办查询数量无效。")
        with self._transaction() as (_connection, cursor):
            cursor.execute(self._sql(
                "SELECT operation_id FROM bindings_qqofficial_ops WHERE environment_id=?"
                " AND state IN ('PREPARED','RESERVED','COMMITTED','REJECTED','ABORTED')"
                " AND next_attempt_at IS NOT NULL AND next_attempt_at<=? ORDER BY next_attempt_at,operation_id LIMIT ?"),
                (environment_id, _now(), limit))
            ids = [r["operation_id"] if isinstance(r, dict) else r[0] for r in cursor.fetchall()]
            return [self._public_operation(self._operation(cursor, op, lock=False)) for op in ids]

    def defer(self, *, environment_id, group_id, operation_id, expected_attempts=None):
        environment_id, operation_id = uuid_value(environment_id), uuid_value(operation_id)
        identifier(group_id)
        if expected_attempts is not None and (type(expected_attempts) is not int or not 0 <= expected_attempts <= MAX_REVISION):
            raise BindingError("INVALID_INPUT", "恢复尝试版本无效。")
        with self._transaction() as (_connection, cursor):
            row = self._scoped(self._operation(cursor, operation_id), environment_id, group_id)
            followup = _decode(row["followup"], {})
            if row["state"] == "PREPARED" and not followup.get("token_reservation"):
                raise BindingError("CONFLICT", "该操作尚未发起预占，不能安排远程恢复。")
            if row["state"] in TERMINAL and not _pending(followup):
                return self._public_operation(row)
            if expected_attempts is not None and int(followup.get("attempts", 0)) != expected_attempts:
                return self._public_operation(row)
            attempt = min(int(followup.get("attempts", 0)) + 1, MAX_REVISION)
            seconds = (2, 10, 30, 60, 300)[min(attempt - 1, 4)]
            following = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S.%f")
            followup["attempts"] = str(attempt)
            cursor.execute(self._sql(
                "UPDATE bindings_qqofficial_ops SET followup=?,next_attempt_at=?,updated_at=? WHERE operation_id=?"),
                (_json(followup), following, _now(), operation_id))
            return self._public_operation(self._operation(cursor, operation_id, lock=False))


def payload_hash(intent):
    """Hash only normalized intent; callers must exclude codes and private text."""
    return hashlib.sha256(canonical_intent_json(intent).encode("utf-8")).hexdigest()


def canonical_intent_json(intent):
    """Cross-language canonical JSON: ASCII keys; strings/null/arrays/objects.

    Revisions must already be decimal strings and arrays already sorted by the
    business normalizer. Reject numbers/bools and unpaired Unicode surrogates.
    """
    def check(value, depth=0):
        if depth > 20:
            raise BindingError("INVALID_INPUT", "操作意图嵌套过深。")
        if value is None:
            return
        if isinstance(value, str):
            if any(0xd800 <= ord(char) <= 0xdfff for char in value):
                raise BindingError("INVALID_INPUT", "操作意图包含无效 Unicode。")
        elif type(value) is list:
            for item in value:
                check(item, depth + 1)
        elif type(value) is dict:
            for key, item in value.items():
                if not isinstance(key, str) or not re.fullmatch(r"[a-z_]+", key):
                    raise BindingError("INVALID_INPUT", "操作意图字段无效。")
                check(item, depth + 1)
        else:
            raise BindingError("INVALID_INPUT", "操作意图仅接受规范字符串值。")
    check(intent)
    return _json(intent)
