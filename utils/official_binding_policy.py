"""Event-scoped authorization for the official center binding workflow."""
from contextlib import contextmanager
import time

from .bind_rule import evaluate
from .official_binding_service import BindingScope, PolicyLease
from .official_bindings import BindingError, GAME_RE, TERMINAL, _revision
from .official_core import OfficialError, OfficialScope, OfficialStore, identifier


class OfficialBindingPolicy:
    """Use the current event only. No event object, code or nickname is persisted."""

    def __init__(self, groups, selection, event_scope, center, *, clock=time.monotonic):
        if (not isinstance(groups, OfficialStore) or not callable(selection)
                or not isinstance(event_scope, OfficialScope) or not callable(getattr(center, "active_snapshot", None))):
            raise TypeError("An official group store, accepted event scope and center repository are required")
        self.groups, self.selection, self.event_scope = groups, selection, event_scope
        self.center, self.clock = center, clock
        self.expires = clock() + 120

    @contextmanager
    def authorized(self, scope):
        """Hold the same lock as the official WebUI configuration writers."""
        sender = self.event_scope
        if (not isinstance(scope, BindingScope)
                or (scope.environment_id, scope.group_id) != (sender.environment_id, sender.group_id)):
            raise BindingError("SCOPE_MISMATCH", "操作不在当前官方群内。")
        with self.groups.lock:
            current = self.selection()
            if (not isinstance(current, dict) or current.get("platform") != "qq_official"
                    or (current.get("environment_id"), current.get("instance_id")) != sender.key[:2]
                    or self.clock() >= self.expires):
                raise BindingError("NOT_SUPPORTED", "当前官方事件已失效，请使用新消息。")
            try:
                state = self.groups.list_state(scope.environment_id)
            except OfficialError:
                raise BindingError("STORAGE_ERROR", "官方群配置暂不可用。") from None
            group = next((row for row in state["groups"] if row["group_openid"] == scope.group_id), None)
            if group is None or not group["enabled"]:
                raise BindingError("UNAUTHORIZED", "当前官方群未启用。")
            level = self.groups.access_level(sender, group, state["environment_administrators"])
            if level == 0:
                raise BindingError("UNAUTHORIZED", "当前身份已被本群禁止使用插件。")
            targets = {server["server_id"] for server in group["servers"] if server["api_port"] is not None}
            if scope.server_id not in targets:
                raise BindingError("SCOPE_MISMATCH", "目标服务器不在当前群的模组配置内。")
            yield group, level, targets

    @contextmanager
    def guard(self, scope, context):
        phase = context["policy_phase"]
        if phase == "access":
            with self.authorized(scope) as (group, _, _):
                yield PolicyLease(str(_revision(group["revision"])))
            return
        kind = context["kind"]
        if kind not in ("bind", "unbind_game", "unbind_user"):
            raise BindingError("INVALID_INPUT", "当前操作种类无效。")
        user_id = identifier(context.get("target_user_id", context.get("user_id")))
        actor_id = identifier(context.get("actor_id"))
        details = context.get("intent", context)
        actor = self.event_scope.user_id
        original_requester = context["actor_kind"] == "qq_member" and actor_id == actor
        with self.authorized(scope) as (group, level, configured):
            revision = str(_revision(group["revision"]))
            if phase in ("inspect", "recover", "cancel"):
                if not (original_requester or actor == user_id or level >= 3):
                    raise BindingError("SCOPE_MISMATCH", "未找到当前身份可查看的操作。")
                if phase == "cancel" and not (original_requester or level >= 3):
                    raise BindingError("UNAUTHORIZED", "当前身份无权中止该操作。")
                if phase == "recover" and context["state"] not in TERMINAL and not original_requester:
                    raise BindingError("NOT_SUPPORTED", "未决绑定需由原请求人在当前群重新核对后继续。")
                yield PolicyLease(revision)
                return

            if phase == "commit" and not original_requester:
                raise BindingError("NOT_SUPPORTED", "需由原请求人在当前群提供新消息，才能继续提交。")
            if not original_requester:
                raise BindingError("UNAUTHORIZED", "操作身份与当前事件不一致。")

            authorization = details["authorization"]["scope"]
            if kind == "bind":
                allowed = authorization == "self" and user_id == actor
            else:
                allowed = ((authorization == "self" and user_id == actor)
                           or (authorization == "group" and level >= 3)
                           or (authorization == "environment" and level == 4))
            if not allowed:
                raise BindingError("UNAUTHORIZED", "当前事件无权使用所请求的授权范围。")

            if phase == "commit" and revision != context["policy_revision"]:
                # The center rejects the old revision under its operation row lock.
                yield PolicyLease(revision)
                return
            targets = details["target_server_ids"]
            if (not isinstance(targets, list) or scope.server_id not in targets
                    or not set(targets) <= configured):
                raise BindingError("SCOPE_MISMATCH", "同步目标超出当前群的模组配置。")

            basis = None
            if (kind == "bind" and group["policy"]["bind_require_nickname"]
                    and phase == "submit" and not details.get("game_id")):
                yield PolicyLease(revision)
                return
            if kind == "bind" and group["policy"]["bind_require_nickname"]:
                if not self.event_scope.nickname:
                    raise BindingError("NOT_SUPPORTED", "当前消息没有可用于校验的群昵称。")
                if phase in ("prepare", "commit"):
                    basis = self._nickname_basis(scope, group["policy"], details["game_id"],
                                                 context.get("nickname_basis") or None, phase)
            yield PolicyLease(revision, basis)

    def _nickname_basis(self, scope, policy, game_id, fixed, phase):
        if not isinstance(game_id, str) or not GAME_RE.fullmatch(game_id):
            raise BindingError("INVALID_INPUT", "取码账号无效。")

        def matches(name):
            return evaluate(self.event_scope.nickname, name, policy["bind_nickname_rule"],
                            policy["bind_nickname_template"])[0]

        if fixed is None and matches(game_id):
            return None
        if phase == "prepare" or fixed is not None:
            try:
                rows = self.center.active_snapshot(scope.environment_id, scope.group_id, self.event_scope.user_id)
            except BindingError:
                raise
            except Exception:
                raise BindingError("DB_UNAVAILABLE", "中心昵称依据暂不可读取；暂停新增绑定。") from None
            for row in rows:
                if row["state"] != "active" or not matches(row["game_id"]):
                    continue
                candidate = {"game_key": row["game_key"], "expected_revision": row["revision"]}
                if fixed is None or candidate == fixed:
                    return candidate
        if phase == "commit" and fixed is not None:
            raise BindingError("CONFLICT", "原昵称校验依据已变化，请重新核对账号后取码。")
        raise BindingError("UNAUTHORIZED", "群昵称须匹配本次取码账号或本人名下仍有效的账号。")
