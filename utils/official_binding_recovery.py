"""Single-worker terminal compensation; never recreates a user authorization.

PREPARED/RESERVED require a fresh requester event for nickname/authorization
checks. This worker only finalizes terminal codes and redelivers COMMITTED fixed
effects. It does not bind, unbind, reserve a raw code or change the target set.
"""
from contextlib import contextmanager
from threading import Lock
import time

from .official_binding_service import BindingScope, OfficialBindingService, PolicyLease
from .official_bindings import BindingError, TERMINAL, _pending
from .official_core import OfficialError
from .official_transport import V2MirrorDelivery


class RecoveryBudget:
    def __init__(self, *, requests=16, seconds=20, clock=time.monotonic):
        self.remaining, self.clock, self.deadline = requests, clock, clock() + seconds

    def consume(self, count):
        if self.remaining < count or self.clock() >= self.deadline:
            raise BindingError("LIMIT_EXCEEDED", "本轮补偿预算已用完。")
        self.remaining -= count


class OfficialFollowupRecovery:
    def __init__(self, host, *, gate=None, delivery_factory=V2MirrorDelivery, clock=time.monotonic):
        self.host, self.gate = host, gate if gate is not None else Lock()
        self.delivery_factory, self.clock = delivery_factory, clock

    @staticmethod
    def _terminal_guard(context, operation):
        @contextmanager
        def guard(scope, intent):
            if (scope.environment_id, scope.group_id) != (context.environment_id, operation["group_id"]):
                raise BindingError("SCOPE_MISMATCH", "补偿范围不匹配。")
            with context.host.groups.lock:
                context.group_guard(scope.group_id)
                phase = intent["policy_phase"]
                if phase != "access" and not (phase in ("recover", "inspect")
                        and intent["state"] in TERMINAL and intent["operation_id"] == operation["operation_id"]):
                    raise BindingError("NOT_SUPPORTED", "后台不能代替本人提交未决变更。")
                yield PolicyLease(str(context.groups[scope.group_id]["revision"]))
        return guard

    def run_once(self, *, limit=8, requests=16, seconds=20):
        if (type(limit) is not int or not 1 <= limit <= 8
                or type(requests) is not int or not 1 <= requests <= 16
                or type(seconds) not in (int, float) or not 0 < seconds <= 20):
            raise ValueError("Bounded recovery settings required")
        counts = dict(state="idle", examined=0, token_confirmed=0, delivered=0, waiting_user=0, failed=0, budget_exhausted=False)
        if not self.gate.acquire(blocking=False):
            return dict(counts, state="busy")
        try:
            context = self.host.recovery()
            budget = RecoveryBudget(requests=requests, seconds=seconds, clock=self.clock)
            operations = context.center.due_operations(context.environment_id, limit=limit)
            counts["state"] = "ready"
            for operation in operations:
                counts["examined"] += 1
                try:
                    context.group_guard(operation["group_id"])
                    if operation["state"] not in TERMINAL:
                        counts["waiting_user"] += 1
                        # Move durable recovery intent out of the hot polling path;
                        # no PREPARED/RESERVED business commit runs without a user.
                        self._defer(context, operation)
                        continue
                    self._terminal(context, operation, budget, counts)
                    latest = context.center.get_operation(context.environment_id, operation["operation_id"], group_id=operation["group_id"])
                    if latest is not None and _pending(latest["followup"]):
                        self._defer(context, operation)
                except BindingError as error:
                    self._defer(context, operation)
                    if error.code == "LIMIT_EXCEEDED":
                        counts["budget_exhausted"] = True
                        break
                    counts["failed"] += 1
                except Exception:
                    self._defer(context, operation)
                    counts["failed"] += 1
            return counts
        except OfficialError as error:
            return dict(counts, state="not_ready", code=error.code if isinstance(error, BindingError) else "STORAGE_ERROR")
        except Exception:
            return dict(counts, state="not_ready", code="STORAGE_ERROR")
        finally:
            self.gate.release()

    def retry_operation(self, context, operation):
        """Explicit trusted-admin retry of one immutable terminal operation."""
        if operation["state"] not in TERMINAL or operation["environment_id"] != context.environment_id:
            raise BindingError("NOT_SUPPORTED", "未决变更须由原请求人在官方群重新核对。")
        if not self.gate.acquire(blocking=False):
            raise BindingError("LIMIT_EXCEEDED", "补偿任务正在运行，请稍后查询原操作。")
        counts = dict(token_confirmed=0, delivered=0, failed=0, budget_exhausted=False)
        try:
            context.group_guard(operation["group_id"])
            latest = context.center.get_operation(context.environment_id, operation["operation_id"], group_id=operation["group_id"])
            if (latest is None or latest["state"] != operation["state"]
                    or latest["payload_hash"] != operation["payload_hash"]):
                raise BindingError("CONFLICT", "原操作状态已变化，请重新查询。")
            try:
                self._terminal(context, latest, RecoveryBudget(clock=self.clock), counts)
            except BindingError as error:
                if error.code != "LIMIT_EXCEEDED":
                    raise
                counts["budget_exhausted"] = True
            finally:
                self._defer(context, latest)
            return counts
        finally:
            self.gate.release()

    @staticmethod
    def _defer(context, operation):
        try:
            context.revalidate()
            context.center.defer(environment_id=context.environment_id, group_id=operation["group_id"],
                                 operation_id=operation["operation_id"],
                                 expected_attempts=int(operation["followup"].get("attempts", 0)))
        except Exception:
            pass  # A failed ACK/backoff never changes or replays the business result.

    def _terminal(self, context, operation, budget, counts):
        group, operation_id = operation["group_id"], operation["operation_id"]
        token = operation["followup"].get("token_finalize")
        if operation["kind"] == "bind" and token and token["state"] == "PENDING":
            try:
                scope = BindingScope(context.environment_id, group, operation["original_server_id"])
                peer = context.peer(group, scope.server_id, budget)
                budget.consume(2)  # At most a status query and one finalize.
                service = OfficialBindingService(context.center, peer, policy_guard=self._terminal_guard(context, operation))
                result = service.recover(scope, operation_id)
                latest = result.get("operation")
                confirmed = latest and latest["followup"].get("token_finalize")
                if confirmed and confirmed["state"] in ("CONSUMED", "INVALIDATED"):
                    counts["token_confirmed"] += 1
                else:
                    counts["failed"] += 1
            except BindingError as error:
                if error.code == "LIMIT_EXCEEDED":
                    raise
                counts["failed"] += 1
            except Exception:
                counts["failed"] += 1
        if operation["state"] != "COMMITTED":
            return
        for server_id, delivery in sorted(operation["followup"]["delivery"].items()):
            if all(delivery[part] in ("APPLIED", "SUPERSEDED") for part in ("mirror", "whitelist", "online")):
                continue
            try:
                context.group_guard(group)
                peer = context.peer(group, server_id, budget)
                budget.consume(1)
                scope = BindingScope(context.environment_id, group, server_id)
                result = self.delivery_factory(context.center, peer).deliver(scope, operation_id)
                if result.get("success") and not result.get("followup_code"):
                    counts["delivered"] += 1
                else:
                    counts["failed"] += 1
            except BindingError as error:
                if error.code == "LIMIT_EXCEEDED":
                    raise
                counts["failed"] += 1
            except Exception:
                counts["failed"] += 1
