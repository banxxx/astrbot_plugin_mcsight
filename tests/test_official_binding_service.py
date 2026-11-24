"""Synthetic orchestration tests; peer calls are in-memory and never contain real codes."""
import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch

import test_official_readonly as prior
import test_official_bindings as binding_tests

core = prior.core
bindings_module = binding_tests.bindings_module
service_module = prior.module("official_binding_service")
policy_module = prior.module("official_binding_policy")
ENV, GROUP, USER = prior.ENV, prior.GROUP, prior.USER
SERVER = binding_tests.SERVER
OP = binding_tests.OP_A
TOKEN = binding_tests.TOKEN_A
RES = binding_tests.RES_A


class Peer:
    def __init__(self, reserve_state="RESERVED", finalize_state="CONSUMED", status_state=None):
        self.reserve_state, self.finalize_state = reserve_state, finalize_state
        self.status_state = status_state
        self.calls = []
        self.stored = None

    def validate(self, scope, code, server):
        self.calls.append(("validate", server))
        return {"state": "FOUND", "scope": scope.wire(), "game_id": "Player_A",
                "game_key": "player_a", "expires_at": "2099-01-01T00:00:00Z", "token_generation": TOKEN}

    def receipt(self, scope, operation, state):
        return {"state": state, "scope": scope.wire(), "operation_id": operation["operation_id"],
            "user_id": operation["target_user_id"], "game_key": operation["intent"]["game_key"],
            "reservation_id": None if state == "ABSENT" else RES, "token_generation": TOKEN}

    def reserve(self, scope, operation, code):
        self.calls.append(("reserve", operation["operation_id"]))
        self.stored = deepcopy(operation)
        if self.reserve_state == "UNKNOWN":
            return {"state": "UNKNOWN", "scope": scope.wire()}
        if self.reserve_state == "UNSUPPORTED":
            return {"state": "UNSUPPORTED", "scope": scope.wire()}
        return self.receipt(scope, operation, "RESERVED")

    def finalize(self, scope, operation, decision):
        self.calls.append(("finalize", decision))
        state = "INVALIDATED" if decision == "ABORTED" else self.finalize_state
        return self.receipt(scope, operation, state)

    def status(self, scope, operation):
        self.calls.append(("status", operation["operation_id"]))
        state = self.status_state or ("RESERVED" if operation["state"] in ("PREPARED", "RESERVED") else self.finalize_state)
        return self.receipt(scope, operation, state)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="official-binding-service-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "center.sqlite3"
        with self.db() as connection:
            connection.executescript(binding_tests.SCHEMA)
        self.store = bindings_module.OfficialBindings(lambda: sqlite3.connect(self.path), placeholder="?")
        self.scope = {"environment_id": ENV, "group_id": GROUP, "server_id": SERVER}
        self.request = {
            "operation_id": OP, "request_key": hashlib.sha256(b"synthetic-bind").hexdigest(),
            "actor_kind": "qq_member", "actor_id": USER, "user_id": USER,
            "authorization": {"scope": "self", "policy_revision": "1"},
            "policy_revision": "1", "target_server_ids": [SERVER], "token_code": "123456",
        }

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def policy(self, scope, intent):
        self.assertNotIn("token_code", intent)
        yield "1"

    def service(self, peer, store=None, guard=None):
        return service_module.OfficialBindingService(store or self.store, peer, policy_guard=guard or self.policy)


    def test_bind_commits_and_finalizes_without_returning_code(self):
        peer = Peer()
        result = self.service(peer).bind(self.scope, self.request)
        self.assertEqual(result["code"], "SYNC_PENDING")
        self.assertNotIn("123456", str(result))
        self.assertEqual([kind for kind, _ in peer.calls], ["validate", "reserve", "finalize"])
        self.assertTrue(peer.stored["followup"]["token_reservation"])
        self.assertTrue(result["success"])
        self.assertNotIn("token_code", str(peer.stored))

    def test_reserve_unknown_persists_recovery_marker_and_status_recovers_reservation(self):
        peer = Peer(reserve_state="UNKNOWN")
        service = self.service(peer)
        result = service.bind(self.scope, self.request)
        self.assertEqual(result["code"], "RESULT_UNKNOWN")
        operation = self.store.get_operation(ENV, OP, group_id=GROUP)
        self.assertEqual(operation["followup"]["token_reservation"]["state"], "PENDING")
        restarted = bindings_module.OfficialBindings(lambda: sqlite3.connect(self.path), placeholder="?")
        recovered = self.service(peer, restarted).recover(self.scope, OP)
        self.assertEqual(recovered["code"], "SYNC_PENDING")
        self.assertEqual(recovered["operation"]["state"], "COMMITTED")
        self.assertEqual(recovered["operation"]["followup"]["token_finalize"]["state"], "CONSUMED")

    def test_unknown_finalize_keeps_committed_operation_recoverable(self):
        peer = Peer(finalize_state="UNKNOWN")
        result = self.service(peer).bind(self.scope, self.request)
        self.assertEqual(result["code"], "SYNC_PENDING")
        self.assertEqual(result["followup_code"], "RESULT_UNKNOWN")
        self.assertTrue(result["success"])
        self.assertEqual(result["operation"]["state"], "COMMITTED")
        peer.finalize_state = "CONSUMED"
        recovered = self.service(peer).recover(self.scope, OP)
        self.assertEqual(recovered["code"], "SYNC_PENDING")
        self.assertEqual(recovered["operation"]["followup"]["token_finalize"]["state"], "CONSUMED")

    def test_unsupported_peer_does_not_create_binding_or_fallback_to_old_storage(self):
        result = self.service(Peer(reserve_state="UNSUPPORTED")).bind(self.scope, self.request)
        self.assertEqual(result["code"], "NOT_SUPPORTED")
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM bindings_qqofficial").fetchone()[0], 0)

    def test_explicit_unbind_uses_fixed_targets_and_does_not_call_peer(self):
        request = {
            "operation_id": binding_tests.OP_C, "request_key": "d" * 64,
            "actor_kind": "qq_member", "actor_id": USER, "target_user_id": USER,
            "kind": "unbind_game", "targets": [{"game_key": "player_a", "expected_revision": "1"}],
            "policy_revision": "1", "authorization": {"scope": "self", "policy_revision": "1"},
            "target_server_ids": [SERVER],
        }
        service = self.service(Peer())
        self.store.prepare_bind(operation_id=OP, request_key=hashlib.sha256(b"seed").hexdigest(),
            environment_id=ENV, group_id=GROUP, actor_kind="qq_member", actor_id=USER, user_id=USER,
            server_id=SERVER, token_generation=TOKEN, game_id="Player_A", policy_revision="1",
            target_server_ids=[SERVER], authorization={"scope": "self", "policy_revision": "1"})
        self.store.reserve(environment_id=ENV, group_id=GROUP, operation_id=OP,
            reservation_id=RES, token_generation=TOKEN)
        self.store.commit_bind(environment_id=ENV, group_id=GROUP, operation_id=OP, policy_revision="1")
        result = service.unbind(self.scope, request)
        self.assertEqual(result["code"], "SYNC_PENDING")
        self.assertFalse(service.peer.calls)

    def test_timeout_exception_after_reserve_has_durable_marker_before_request(self):
        peer = Peer()
        def timeout(scope, operation, _code):
            stored = self.store.get_operation(ENV, OP, group_id=GROUP)
            self.assertEqual(stored["followup"]["token_reservation"]["state"], "PENDING")
            raise TimeoutError("SYNTHETIC_PRIVATE_DETAIL")
        peer.reserve = timeout
        result = self.service(peer).bind(self.scope, self.request)
        self.assertEqual((result["code"], result["state"]), ("RESULT_UNKNOWN", "PREPARED"))
        self.assertNotIn("SYNTHETIC_PRIVATE_DETAIL", str(result))
        self.assertEqual(self.store.get_operation(ENV, OP, group_id=GROUP)["followup"]["attempts"], "1")

    def test_process_crash_during_reserve_is_recovered_by_new_service_without_code(self):
        class Crash(BaseException): pass
        peer = Peer(status_state="RESERVED")
        def crash(*_): raise Crash()
        peer.reserve = crash
        with self.assertRaises(Crash): self.service(peer).bind(self.scope, self.request)
        due = self.store.due_operations(ENV)
        self.assertEqual([row["operation_id"] for row in due], [OP])
        peer.validate = prior.forbidden
        recovered = self.service(peer).recover(self.scope, OP)
        self.assertEqual(recovered["state"], "COMMITTED")
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT revision FROM bindings_qqofficial").fetchall(), [(1,)])

    def test_crash_after_reserved_state_remains_discoverable_and_recovers_to_commit(self):
        class Crash(BaseException): pass
        with patch.object(self.store, "commit_bind", side_effect=Crash()):
            with self.assertRaises(Crash): self.service(Peer()).bind(self.scope, self.request)
        self.assertEqual(self.store.due_operations(ENV)[0]["state"], "RESERVED")
        peer = Peer()
        peer.reserve = peer.validate = prior.forbidden
        self.assertTrue(self.service(peer).recover(self.scope, OP)["success"])

    def test_finalize_network_failure_keeps_center_success_and_retry_finalizes_again(self):
        peer = Peer()
        def timeout(*_): raise TimeoutError("SYNTHETIC_PRIVATE_DETAIL")
        peer.finalize = timeout
        result = self.service(peer).bind(self.scope, self.request)
        self.assertEqual((result["code"], result["state"], result["success"]), ("SYNC_PENDING", "COMMITTED", True))
        peer.finalize = Peer().finalize
        recovered = self.service(peer).recover(self.scope, OP)
        self.assertEqual(recovered["operation"]["followup"]["token_finalize"]["state"], "CONSUMED")

    def test_commit_ack_loss_recovers_success_without_second_binding(self):
        real_commit = self.store.commit_bind
        def commit_lost(**values):
            real_commit(**values)
            raise bindings_module.BindingError("RESULT_UNKNOWN", "SYNTHETIC_PRIVATE_DETAIL")
        with patch.object(self.store, "commit_bind", side_effect=commit_lost):
            result = self.service(Peer()).bind(self.scope, self.request)
        self.assertTrue(result["success"])
        self.assertEqual(result["state"], "COMMITTED")
        self.assertNotIn("SYNTHETIC_PRIVATE_DETAIL", str(result))
        recovered = self.service(Peer()).recover(self.scope, OP)
        self.assertTrue(recovered["success"])

    def test_duplicate_event_returns_original_operation_without_validate_or_reserve(self):
        service = self.service(Peer())
        first = service.bind(self.scope, self.request)
        service.peer.validate = service.peer.reserve = service.peer.finalize = prior.forbidden
        duplicate = service.bind(self.scope, dict(self.request, operation_id=binding_tests.OP_B, token_code="654321"))
        self.assertEqual(duplicate["operation_id"], first["operation_id"])
        self.assertTrue(duplicate["success"])

    def test_new_event_same_generation_uses_original_operation_id(self):
        peer = Peer(reserve_state="UNKNOWN")
        self.service(peer).bind(self.scope, self.request)
        result = self.service(peer).bind(self.scope, dict(self.request, operation_id=binding_tests.OP_B, request_key="b" * 64))
        self.assertEqual(result["operation_id"], OP)
        self.assertTrue(result["success"])
        self.assertEqual(sum(kind == "reserve" for kind, _ in peer.calls), 1)

    def test_foreign_receipt_or_wrong_recovery_server_never_commits(self):
        peer = Peer()
        original = peer.validate
        def foreign(scope, *args):
            result = original(scope, *args)
            result["scope"]["server_id"] = binding_tests.TOKEN_B
            return result
        peer.validate = foreign
        result = self.service(peer).bind(self.scope, self.request)
        self.assertEqual(result["code"], "SCOPE_MISMATCH")
        self.assertIsNone(self.store.get_operation(ENV, OP, group_id=GROUP))
        self.service(Peer()).bind(self.scope, self.request)
        denied = self.service(Peer()).recover(dict(self.scope, server_id=binding_tests.TOKEN_B), OP)
        self.assertEqual(denied["code"], "SCOPE_MISMATCH")
        self.assertNotIn("operation", denied)

    def test_finalize_mismatched_generation_never_clears_pending_confirmation(self):
        peer = Peer()
        original = peer.finalize
        def foreign(*args):
            result = original(*args)
            result["token_generation"] = binding_tests.TOKEN_B
            return result
        peer.finalize = foreign
        result = self.service(peer).bind(self.scope, self.request)
        self.assertTrue(result["success"])
        self.assertEqual(result["followup_code"], "CONFLICT")
        self.assertEqual(self.store.get_operation(ENV, OP, group_id=GROUP)["followup"]["token_finalize"]["state"], "PENDING")

    def test_policy_revocation_after_reserve_cas_aborts_before_remote_invalidation(self):
        calls = []
        @contextmanager
        def guard(scope, intent):
            calls.append(intent)
            if intent.get("state") == "RESERVED":
                raise bindings_module.BindingError("UNAUTHORIZED", "revoked")
            yield "1"
        peer = Peer()
        original = peer.finalize
        def finalize(scope, operation, decision):
            self.assertEqual(self.store.get_operation(ENV, OP, group_id=GROUP)["state"], "ABORTED")
            return original(scope, operation, decision)
        peer.finalize = finalize
        result = self.service(peer, guard=guard).bind(self.scope, self.request)
        self.assertEqual(result["state"], "ABORTED")
        self.assertFalse(result["success"])
        self.assertEqual(result["operation"]["followup"]["token_finalize"]["state"], "INVALIDATED")

    def test_policy_change_on_restart_rejects_reserved_operation(self):
        self.service(Peer(reserve_state="UNKNOWN")).bind(self.scope, self.request)
        @contextmanager
        def changed(*_): yield "2"
        result = self.service(Peer(status_state="RESERVED"), guard=changed).recover(self.scope, OP)
        self.assertEqual(result["state"], "REJECTED")
        self.assertEqual(result["code"], "CONFLICT")

    def test_missing_guard_and_invalid_code_refuse_before_any_peer_or_storage(self):
        peer = Peer()
        result = service_module.OfficialBindingService(self.store, peer).bind(self.scope, self.request)
        self.assertEqual(result["code"], "NOT_SUPPORTED")
        for code in ("12345", "１２３４５６", "123456 ", 123456):
            result = self.service(peer).bind(self.scope, dict(self.request, token_code=code))
            self.assertEqual(result["code"], "INVALID_INPUT")
        self.assertEqual(peer.calls, [])
        self.assertIsNone(self.store.get_operation(ENV, OP, group_id=GROUP))

    def test_absent_local_prepared_reservation_cas_aborts_then_creates_tombstone(self):
        peer = Peer(reserve_state="UNKNOWN", status_state="ABSENT")
        service = self.service(peer)
        self.assertEqual(service.bind(self.scope, self.request)["state"], "PREPARED")
        original = peer.finalize
        def observed(scope, operation, decision):
            self.assertEqual(self.store.get_operation(ENV, OP, group_id=GROUP)["state"], "ABORTED")
            return original(scope, operation, decision)
        peer.finalize = observed
        result = service.recover(self.scope, OP)
        self.assertEqual(result["state"], "ABORTED")
        self.assertEqual(result["operation"]["followup"]["token_finalize"]["state"], "INVALIDATED")
        self.assertNotIn("token_code", str(result))

    def test_absent_local_reserved_record_never_proceeds_to_binding_commit(self):
        class Crash(BaseException): pass
        peer = Peer(status_state="ABSENT")
        service = self.service(peer)
        with patch.object(self.store, "commit_bind", side_effect=Crash()):
            with self.assertRaises(Crash): service.bind(self.scope, self.request)
        result = service.recover(self.scope, OP)
        self.assertEqual(result["state"], "ABORTED")
        self.assertFalse(result["success"])
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM bindings_qqofficial").fetchone()[0], 0)

    def test_center_down_prevents_cas_abort_and_no_mod_finalization_is_sent(self):
        peer = Peer(reserve_state="UNKNOWN", status_state="ABSENT")
        service = self.service(peer)
        service.bind(self.scope, self.request)
        with patch.object(self.store, "abort", side_effect=bindings_module.BindingError("DB_UNAVAILABLE", "synthetic")):
            result = service.recover(self.scope, OP)
        self.assertEqual((result["code"], result["state"]), ("DB_UNAVAILABLE", "PREPARED"))
        self.assertFalse(any(kind == "finalize" for kind, _ in peer.calls))
        self.assertEqual(self.store.get_operation(ENV, OP, group_id=GROUP)["state"], "PREPARED")

    def test_committed_center_missing_mod_reservation_keeps_success_but_stops_consumption(self):
        peer = Peer(finalize_state="UNKNOWN", status_state="ABSENT")
        service = self.service(peer)
        service.bind(self.scope, self.request)
        before = len(peer.calls)
        result = service.recover(self.scope, OP)
        self.assertEqual((result["success"], result["state"], result["code"]), (True, "COMMITTED", "SYNC_PENDING"))
        self.assertEqual(result["followup_code"], "STORAGE_ERROR")
        self.assertEqual([kind for kind, _ in peer.calls[before:]], ["status"])
        self.assertEqual(self.store.get_operation(ENV, OP, group_id=GROUP)["followup"]["token_finalize"]["state"], "PENDING")

    def test_finalize_ack_lost_uses_persisted_mod_consumed_status_without_resending(self):
        peer = Peer(finalize_state="UNKNOWN", status_state="CONSUMED")
        service = self.service(peer)
        service.bind(self.scope, self.request)
        peer.finalize = prior.forbidden
        result = service.recover(self.scope, OP)
        self.assertTrue(result["success"])
        self.assertEqual(result["operation"]["followup"]["token_finalize"]["state"], "CONSUMED")

    def test_cancel_before_reserve_dispatch_cas_aborts_and_invalidates_absent_generation(self):
        self.store.prepare_bind(operation_id=OP, request_key=self.request["request_key"], environment_id=ENV, group_id=GROUP,
            actor_kind="qq_member", actor_id=USER, user_id=USER, server_id=SERVER, token_generation=TOKEN,
            game_id="Player_A", policy_revision="1", target_server_ids=[SERVER], authorization=self.request["authorization"])
        peer = Peer(status_state="ABSENT")
        service = self.service(peer)
        peer.validate = peer.reserve = prior.forbidden
        result = service.cancel(self.scope, OP)
        self.assertEqual(result["state"], "ABORTED")
        self.assertEqual(result["operation"]["followup"]["token_finalize"]["state"], "INVALIDATED")

    def test_cancel_already_committed_can_only_finish_committed_confirmation(self):
        peer = Peer()
        service = self.service(peer)
        service.bind(self.scope, self.request)
        result = service.cancel(self.scope, OP)
        self.assertTrue(result["success"])
        self.assertEqual(result["state"], "COMMITTED")
        self.assertEqual(result["operation"]["followup"]["token_finalize"]["state"], "CONSUMED")

    def test_foreign_cancel_cannot_revoke_or_reveal_other_server_operation(self):
        service = self.service(Peer())
        service.bind(self.scope, self.request)
        result = service.cancel(dict(self.scope, server_id=binding_tests.TOKEN_B), OP)
        self.assertEqual(result["code"], "SCOPE_MISMATCH")
        self.assertNotIn("operation", result)
        self.assertEqual(self.store.get_operation(ENV, OP, group_id=GROUP)["state"], "COMMITTED")


class EventPolicyGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="official-policy-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.groups = core.OfficialStore(Path(self.temp.name))
        self.addCleanup(self.groups.close)
        self.event = core.event_scope(prior.Event(message_id="SYNTHETIC_POLICY_EVENT"), prior.SELECTION)
        candidate = self.groups.register(self.event, "synthetic")
        group = self.groups.approve(ENV, candidate["candidate_id"], self.groups.list_state(ENV)["revision"],
                                   "Synthetic", "123456789")
        group = self.groups.update(ENV, group["config_id"], group["revision"], {"enabled": True, "servers": [
            {"server_id": "", "name": "one", "host": "mc.example.invalid", "game_port": 25565,
             "api_port": 8080, "hidden": False, "remark": ""}]})
        self.scope = service_module.BindingScope(ENV, GROUP, group["servers"][0]["server_id"])

    def guard(self, event=None, rows=(), now=lambda: 100.0):
        center = type("Center", (), {"active_snapshot": lambda _self, env, group, user: list(rows)})()
        policy = policy_module.OfficialBindingPolicy(self.groups, lambda: prior.SELECTION,
                    event or self.event, center, clock=now)
        return policy.guard

    def intent(self):
        return {"kind": "bind", "actor_kind": "qq_member", "actor_id": USER, "user_id": USER,
                "authorization": {"scope": "self"}, "target_server_ids": [self.scope.server_id],
                "game_id": "Player_A", "policy_revision": "2", "policy_phase": "prepare"}

    def test_current_group_card_is_checked_against_the_resolved_account(self):
        with self.guard()(self.scope, self.intent()) as lease:
            self.assertIsNone(lease.nickname_basis)
        for card in ("", "玩家（Player_X）"):
            event = core.OfficialScope(ENV, "official-a", GROUP, USER, "SYNTHETIC_BAD_CARD", card, "member")
            with self.assertRaises(core.OfficialError):
                with self.guard(event)(self.scope, self.intent()): pass

    def test_other_current_active_account_is_an_explicit_revision_basis(self):
        event = core.OfficialScope(ENV, "official-a", GROUP, USER, "SYNTHETIC_ALIAS", "玩家(Player_B)", "member")
        rows = [{"game_id": "Player_B", "game_key": "player_b", "user_id": USER,
                 "state": "active", "revision": "7"}]
        with self.guard(event, rows)(self.scope, self.intent()) as lease:
            self.assertEqual(lease.nickname_basis, {"game_key": "player_b", "expected_revision": "7"})

    def test_runtime_factory_uses_real_event_policy_through_the_center_workflow(self):
        fixture = ServiceTests("test_bind_commits_and_finalizes_without_returning_code")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        class Activation:
            @staticmethod
            def status():
                return {"state": "configuring", "selection": prior.SELECTION,
                        "environments": [{"environment_id": ENV, "platform": "qq_official"}]}
            @staticmethod
            def accepts(event):
                return core.event_scope(event, prior.SELECTION) is not None
        runtime = prior.runtime_module.OfficialRuntime(self.temp.name, Activation())
        runtime.store = self.groups
        self.addCleanup(runtime.close)
        request = dict(fixture.request, policy_revision="2", target_server_ids=[self.scope.server_id],
                       authorization={"scope": "self", "policy_revision": "2"})
        service = runtime.binding_service_for(prior.Event(), center=fixture.store, peer=Peer())
        result = service.bind(self.scope, request)
        self.assertTrue(result["success"], result["code"])
        self.assertEqual(fixture.store.check_game(ENV, GROUP, "Player_A")["state"], "active")
        self.assertNotIn(self.event.nickname, str(result["operation"]))
        runtime.close()
        with self.assertRaises(core.OfficialError):
            runtime.binding_service_for(prior.Event(), center=fixture.store, peer=Peer())

    def test_expired_event_and_blacklist_cannot_acquire_binding_authority(self):
        now = [100.0]
        guard = self.guard(now=lambda: now[0])
        with guard(self.scope, self.intent()): pass
        now[0] = 221.0
        with self.assertRaises(core.OfficialError):
            with guard(self.scope, self.intent()): pass
        group = self.groups.list_state(ENV)["groups"][0]
        candidate = self.groups.register(self.event, "synthetic blacklist candidate")
        self.groups.authorize(ENV, group["config_id"], candidate["candidate_id"], "blacklist", group["revision"])
        with self.assertRaises(core.OfficialError):
            with self.guard()(self.scope, self.intent()): pass

    def test_real_event_policy_aborts_a_reserved_bind_when_its_nickname_basis_is_revoked(self):
        fixture = ServiceTests("test_bind_commits_and_finalizes_without_returning_code")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        center = fixture.store
        server = self.scope.server_id
        auth = {"scope": "self", "policy_revision": "2"}
        center.prepare_bind(operation_id=binding_tests.OP_C, request_key="c" * 64,
            environment_id=ENV, group_id=GROUP, actor_kind="qq_member", actor_id=USER, user_id=USER,
            server_id=server, token_generation=binding_tests.TOKEN_B, game_id="Player_B",
            policy_revision="2", target_server_ids=[server], authorization=auth)
        center.claim_reservation(environment_id=ENV, group_id=GROUP, operation_id=binding_tests.OP_C,
                                 token_generation=binding_tests.TOKEN_B)
        center.reserve(environment_id=ENV, group_id=GROUP, operation_id=binding_tests.OP_C,
                       reservation_id=binding_tests.RES_B, token_generation=binding_tests.TOKEN_B)
        center.commit_bind(environment_id=ENV, group_id=GROUP, operation_id=binding_tests.OP_C, policy_revision="2")
        member = core.OfficialScope(ENV, "official-a", GROUP, USER, "SYNTHETIC_BASIS_RACE",
                                    "玩家(Player_B)", "member")
        guard = policy_module.OfficialBindingPolicy(self.groups, lambda: prior.SELECTION, member, center).guard
        peer = Peer()
        original = peer.reserve
        def reserve_then_revoke(scope, operation, code):
            receipt = original(scope, operation, code)
            center.prepare_unbind(operation_id=binding_tests.OP_B, request_key="b" * 64,
                environment_id=ENV, group_id=GROUP, actor_kind="qq_member", actor_id=USER,
                target_user_id=USER, kind="unbind_game",
                targets=[{"game_key": "player_b", "expected_revision": "1"}],
                policy_revision="2", authorization=auth, target_server_ids=[server])
            center.commit_unbind(environment_id=ENV, group_id=GROUP, operation_id=binding_tests.OP_B,
                                 policy_revision="2")
            return receipt
        peer.reserve = reserve_then_revoke
        workflow = service_module.OfficialBindingService(center, peer, policy_guard=guard)
        request = dict(fixture.request, policy_revision="2", authorization=auth, target_server_ids=[server])
        result = workflow.bind(self.scope, request)
        self.assertEqual((result["state"], result["code"]), ("ABORTED", "CONFLICT"))
        self.assertEqual(peer.calls[-1], ("finalize", "ABORTED"))
        self.assertFalse(center.check_game(ENV, GROUP, "Player_A")["bound"])

    def test_other_member_cannot_inspect_and_admin_can_cancel_but_not_complete_a_pending_bind(self):
        fixture = ServiceTests("test_bind_commits_and_finalizes_without_returning_code")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        peer = Peer(reserve_state="UNKNOWN", status_state="ABSENT")
        def workflow(member):
            guard = policy_module.OfficialBindingPolicy(self.groups, lambda: prior.SELECTION,
                                                        member, fixture.store).guard
            return service_module.OfficialBindingService(fixture.store, peer, policy_guard=guard)
        request = dict(fixture.request, policy_revision="2", target_server_ids=[self.scope.server_id],
                       authorization={"scope": "self", "policy_revision": "2"})
        self.assertEqual(workflow(self.event).bind(self.scope, request)["state"], "PREPARED")
        calls = list(peer.calls)
        outsider = core.OfficialScope(ENV, "official-a", GROUP, "SYNTHETIC_OTHER_MEMBER",
                                      "SYNTHETIC_INSPECT", "", "member")
        hidden = workflow(outsider).inspect(self.scope, OP)
        self.assertEqual(hidden["code"], "SCOPE_MISMATCH")
        self.assertNotIn("operation", hidden)
        admin = core.OfficialScope(ENV, "official-a", GROUP, "SYNTHETIC_GROUP_ADMIN",
                                   "SYNTHETIC_CANCEL", "", "admin")
        self.assertEqual(workflow(admin).inspect(self.scope, OP)["state"], "PREPARED")
        blocked = workflow(admin).recover(self.scope, OP)
        self.assertEqual(blocked["code"], "NOT_SUPPORTED")
        self.assertNotIn("operation", blocked)
        self.assertEqual(peer.calls, calls)
        cancelled = workflow(admin).cancel(self.scope, OP)
        self.assertEqual(cancelled["state"], "ABORTED")
        self.assertEqual(peer.calls[-1], ("finalize", "ABORTED"))
        self.assertNotIn("123456", str(cancelled))

    def test_missing_card_keeps_reservation_pending_until_a_fresh_reliable_event(self):
        fixture = ServiceTests("test_bind_commits_and_finalizes_without_returning_code")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        peer = Peer(reserve_state="UNKNOWN", status_state="RESERVED")
        def workflow(member):
            guard = policy_module.OfficialBindingPolicy(self.groups, lambda: prior.SELECTION,
                                                        member, fixture.store).guard
            return service_module.OfficialBindingService(fixture.store, peer, policy_guard=guard)
        request = dict(fixture.request, policy_revision="2", target_server_ids=[self.scope.server_id],
                       authorization={"scope": "self", "policy_revision": "2"})
        self.assertEqual(workflow(self.event).bind(self.scope, request)["state"], "PREPARED")
        missing = core.OfficialScope(ENV, "official-a", GROUP, USER, "SYNTHETIC_NO_CARD", "", "member")
        pending = workflow(missing).recover(self.scope, OP)
        self.assertEqual((pending["state"], pending["code"]), ("RESERVED", "NOT_SUPPORTED"))
        self.assertFalse(any(kind == "finalize" for kind, _ in peer.calls))
        self.assertFalse(fixture.store.check_game(ENV, GROUP, "Player_A")["bound"])
        restored = workflow(self.event).recover(self.scope, OP)
        self.assertEqual(restored["state"], "COMMITTED")
        self.assertTrue(restored["success"])
        self.assertEqual(peer.calls[-1], ("finalize", "COMMITTED"))


if __name__ == "__main__":
    unittest.main()
