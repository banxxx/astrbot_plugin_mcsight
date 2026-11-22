"""Framework-plugin-scope management of existing official operations only.

The registered WebUI route supplies authorization; client actor/role claims are
never accepted. No user event is fabricated, and this surface cannot create or
commit a binding/unbinding operation. Tests use explicit synthetic host/guards.
"""
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from threading import Lock, RLock
import re
import time
from uuid import uuid4

from .official_bindings import BindingError, HEX_64, TERMINAL, _json, _pending
from .official_core import uuid_value
from .official_local import LocalRepository


class OfficialBindingAdmin:
    def __init__(self, host, recovery, authorize=None, *, clock=time.monotonic):
        self.host, self.recovery, self.authorize, self.clock = host, recovery, authorize, clock
        self.lock, self.busy, self.pages = RLock(), Lock(), OrderedDict()
        self.closed = False

    @contextmanager
    def _request(self):
        if not self.busy.acquire(blocking=False):
            raise BindingError("LIMIT_EXCEEDED", "官方操作查询或恢复正在进行，请稍后重试。")
        try:
            yield
        finally:
            self.busy.release()

    def _context(self, environment_id, config_id, expected_revision,server_id=None):
        environment = environment_id
        deadline = self.clock() + 120
        def revalidate():
            if self.clock() >= deadline:
                raise BindingError("UNAUTHORIZED", "该管理请求已失效，请刷新后重试。")
            self.validate_scope(environment, config_id, expected_revision)
        with self.host.groups.lock:
            group = self.validate_scope(environment, config_id, expected_revision)
            context = (self.host.recovery(guard=revalidate,server_id=server_id,group_id=group["group_openid"])
                if server_id is not None else self.host.recovery(guard=revalidate))
            if context.environment_id != environment:
                raise BindingError("SCOPE_MISMATCH", "只能管理当前运行的官方环境。")
        context.group_guard(group["group_openid"])
        return context, group

    def validate_scope(self, environment_id, config_id, expected_revision):
        """Recheck public scope before returning a worker result; no secrets read."""
        environment = environment_id
        if self.closed:
            raise BindingError("NOT_SUPPORTED", "官方操作管理正在卸载。")
        uuid_value(environment)
        uuid_value(config_id)
        if type(expected_revision) is not int or expected_revision < 1:
            raise BindingError("INVALID_INPUT", "群配置修订无效。")
        if not callable(self.authorize) or self.authorize(environment) is False:
            raise BindingError("UNAUTHORIZED", "该管理请求尚未获框架插件范围授权。")
        with self.host.groups.lock:
            group = self.host.groups.group(environment, config_id=config_id)
            if group is None or not group["enabled"]:
                raise BindingError("UNAUTHORIZED", "目标官方群当前未启用。")
            if group["revision"] != expected_revision:
                raise BindingError("CONFLICT", "官方群配置已变化，请刷新配置后重试。")
            return group

    def list(self, environment_id, config_id, expected_revision, *, pending=True, cursor=None,server_id=None):
        environment = environment_id
        if type(pending) is not bool:
            raise BindingError("INVALID_INPUT", "操作筛选无效。")
        with self._request():
            context, group = self._context(environment, config_id, expected_revision,server_id)
            key = environment, config_id, expected_revision, pending,server_id
            with self.lock:
                now = self.clock()
                self.pages = OrderedDict((sid, page) for sid, page in self.pages.items() if page["expires"] > now)
                if cursor is None:
                    rows = context.center.operation_summaries(environment, group["group_openid"], pending=pending, limit=201)
                    context.group_guard(group["group_openid"])
                    _json(rows)
                    sid, offset = str(uuid4()), 0
                    page = dict(key=key, rows=rows[:200], truncated=len(rows) > 200, expires=now + 600)
                    while len(self.pages) >= 16:
                        self.pages.popitem(last=False)
                    self.pages[sid] = page
                else:
                    if not isinstance(cursor, str) or not re.fullmatch(r"[0-9a-f-]{36}:[0-9]{1,3}", cursor):
                        raise BindingError("INVALID_INPUT", "操作分页游标无效。")
                    sid, raw_offset = cursor.split(":")
                    uuid_value(sid)
                    page, offset = self.pages.get(sid), int(raw_offset)
                    if page is None or page["key"] != key or offset < 20 or offset % 20 or offset >= len(page["rows"]):
                        raise BindingError("CONFLICT", "操作分页已过期或范围不同，请重新查询。")
                following = f"{sid}:{offset + 20}" if offset + 20 < len(page["rows"]) else None
                return dict(operations=deepcopy(page["rows"][offset:offset + 20]), next_cursor=following,
                            total=len(page["rows"]), truncated=page["truncated"])

    def inspect(self, environment_id, config_id, expected_revision, operation_id,server_id=None):
        environment = environment_id
        with self._request():
            context, group = self._context(environment, config_id, expected_revision,server_id)
            operation = self._operation(context, group, operation_id)
            context.group_guard(group["group_openid"])
            return self._detail(context, operation)

    @staticmethod
    def _operation(context, group, operation_id):
        if not isinstance(operation_id, str) or not re.fullmatch(r"[0-9a-f-]{8,36}", operation_id):
            raise BindingError("INVALID_INPUT", "请输入完整操作编号或至少8位前缀。")
        # Framework plugin scope already grants configuration management. No
        # client-supplied actor is used as an authorization or audit identity.
        return context.center.resolve_operation(context.environment_id, group["group_openid"], operation_id,
                                                administrator=True)

    def act(self, environment_id, config_id, expected_revision, operation_id, *, action, expected_state, payload_hash, confirmed,server_id=None):
        environment = environment_id
        if (action not in ("retry", "cancel") or confirmed is not True or not isinstance(expected_state, str)
                or expected_state not in TERMINAL | {"PREPARED", "RESERVED"}
                or not isinstance(payload_hash, str) or not HEX_64.fullmatch(payload_hash)):
            raise BindingError("INVALID_INPUT", "请先查询并确认该操作的当前状态。")
        uuid_value(operation_id)
        with self._request():
            context, group = self._context(environment, config_id, expected_revision,server_id)
            with self.host.groups.lock:
                context.group_guard(group["group_openid"])
                operation = self._operation(context, group, operation_id)
                if operation["state"] != expected_state or operation["payload_hash"] != payload_hash:
                    raise BindingError("CONFLICT", "操作状态已变化，请重新查询后确认。")
                if action == "cancel":
                    if operation["state"] in TERMINAL:
                        raise BindingError("CONFLICT", "中心已确定终态，不能中止或回滚。")
                    operation = context.center.abort(environment_id=environment, group_id=group["group_openid"],
                                                     operation_id=operation_id)
                    code = "OK" if operation["state"] == "ABORTED" else "CONFLICT"
                    summary = None
                else:
                    if operation["state"] not in TERMINAL:
                        raise BindingError("NOT_SUPPORTED", "未决绑定/解绑须由原请求人在官方群提供新消息，不支持后台代办提交。")
            if action == "retry":
                if isinstance(context.center,LocalRepository):
                    operation=context.center.retry_terminal(operation_id); summary=None
                else:
                    summary = self.recovery.retry_operation(context, operation)
                operation = self._operation(context, group, operation_id)
                code = "SYNC_PENDING" if _pending(operation["followup"]) else "OK"
            context.group_guard(group["group_openid"])
            return dict(code=code, operation=self._detail(context, operation), recovery=summary)

    @staticmethod
    def _detail(context, operation):
        names = {s["server_id"]: s["name"] for s in context.groups[operation["group_id"]]["servers"]}
        token = operation["followup"].get("token_finalize")
        return dict(operation_id=operation["operation_id"], kind=operation["kind"], state=operation["state"],
            result_code=operation["result_code"], payload_hash=operation["payload_hash"],
            policy_revision=operation["policy_revision"], pending=_pending(operation["followup"]),
            token_state=token["state"] if token else None,
            effects=[{field: effect[field] for field in ("game_id", "game_key", "state", "revision")} for effect in operation["effects"]],
            delivery=[dict(server_id=server, name=names.get(server, "已移除的原目标"),
                mirror=item["mirror"], whitelist=item["whitelist"], online=item["online"])
                for server, item in sorted(operation["followup"]["delivery"].items())])

    def close(self):
        self.closed = True
        if self.lock.acquire(blocking=False):
            try:
                self.pages.clear()
            finally:
                self.lock.release()
