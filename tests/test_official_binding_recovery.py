"""Offline lifecycle/recovery integration, real center and mirror-ACK adapter.

Only explicitly created temp data is used. The wire peer is a memory fake; no
network, framework, real config, environment inspection or subprocess starts.
"""
import asyncio
from contextlib import closing
from dataclasses import replace
import sqlite3
from threading import Lock
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_official_binding_commands as ingress
import test_official_readonly as prior

recovery_module = prior.module("official_binding_recovery")
transport = prior.module("official_transport")
bindings = ingress.bindings
ENV, GROUP, USER = ingress.ENV, ingress.GROUP, ingress.USER


class WirePeer(ingress.Peer, transport.V2TokenPeer):
    def __init__(self, target, guard):
        ingress.Peer.__init__(self)
        self.client, self.revalidate = SimpleNamespace(target=target), guard
        self.whitelist, self.sync_error, self.reserve_unknown = "APPLIED", False, False

    def handshake(self):
        self.revalidate()
        return ingress.Peer.handshake(self)

    def reserve(self, scope, operation, code):
        result = ingress.Peer.reserve(self, scope, operation, code)
        if self.reserve_unknown:
            raise bindings.BindingError("RESULT_UNKNOWN", "synthetic reserve ACK lost")
        return result

    def _request(self, scope, endpoint, payload):
        self.revalidate()
        if endpoint != "sync":
            raise AssertionError("Only the synthetic mirror downlink is allowed")
        self.calls.append("sync")
        if self.sync_error:
            raise bindings.BindingError("RESULT_UNKNOWN", "SYNTHETIC_PRIVATE_DOWNLINK_FAILURE")
        return dict(protocol_version=2, operation_id=payload["operation_id"], results=[dict(
            game_key=effect["game_key"], requested_revision=effect["revision"], effective_revision=effect["revision"],
            result="applied", persisted=True, whitelist=self.whitelist, online="APPLIED") for effect in payload["effects"]])


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ingress.CommandIntegrationTests("test_bind_runs_through_real_service_policy_and_center_without_code_in_result")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        def factory(target, token_source, guard):
            key = target.scope.server_id
            peer = self.fixture.peers.get(key)
            if peer is None:
                peer = WirePeer(target, guard)
                self.fixture.peers[key] = peer
            peer.revalidate = guard
            return peer
        self.fixture.host.peer_factory = factory
        self.worker = recovery_module.OfficialFollowupRecovery(self.fixture.host)

    def peer(self, index=0):
        server = self.fixture.group["servers"][index]
        scope = transport.BindingScope(ENV, GROUP, server["server_id"])
        target = transport.ModTarget(scope, server["host"], server["api_port"])
        return self.fixture.host.peer_factory(target, prior.forbidden, lambda: None)

    def due(self):
        with closing(sqlite3.connect(self.fixture.db_path)) as db:
            db.execute("UPDATE bindings_qqofficial_ops SET next_attempt_at='2000-01-01 00:00:00.000000' WHERE next_attempt_at IS NOT NULL")
            db.commit()

    def scheduled(self):
        operation = self.fixture.operation()
        with closing(sqlite3.connect(self.fixture.db_path)) as db:
            return db.execute("SELECT next_attempt_at FROM bindings_qqofficial_ops WHERE operation_id=?",
                              (operation["operation_id"],)).fetchone()[0]

    def test_committed_code_and_all_effects_recover_without_another_reserve(self):
        self.peer().finalize_unknown = True
        self.fixture.execute()
        self.peer().finalize_unknown = False
        self.due()
        result = self.worker.run_once()
        operation = self.fixture.operation()
        self.assertEqual((result["token_confirmed"], result["delivered"]), (1, 1))
        self.assertEqual(operation["result_code"], "OK")
        self.assertEqual(operation["followup"]["token_finalize"]["state"], "CONSUMED")
        self.assertEqual(self.peer().calls.count("reserve"), 1)
        self.assertIsNone(self.scheduled())
        self.assertNotIn(USER, str(result))
        self.assertNotIn("123456", str(result))

    def test_pending_whitelist_is_not_reported_as_complete_and_gets_backoff(self):
        self.peer().whitelist = "PENDING"
        self.fixture.execute()
        self.due()
        result = self.worker.run_once()
        operation = self.fixture.operation()
        target = next(iter(operation["followup"]["delivery"].values()))
        self.assertEqual(target["mirror"], "APPLIED")
        self.assertEqual(target["whitelist"], "PENDING")
        self.assertEqual(operation["result_code"], "SYNC_PENDING")
        self.assertIsNotNone(self.scheduled())
        self.assertEqual(self.worker.run_once()["examined"], 0)

    def test_one_target_failure_does_not_roll_back_center_or_block_other_targets(self):
        self.fixture.update({"servers": self.fixture.group["servers"] + [prior.server("two")]})
        self.peer().sync_error = True
        self.fixture.execute()
        self.due()
        result = self.worker.run_once()
        operation = self.fixture.operation()
        targets = operation["followup"]["delivery"]
        self.assertEqual(operation["state"], "COMMITTED")
        self.assertEqual(result["delivered"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(targets[self.fixture.group["servers"][1]["server_id"]]["mirror"], "APPLIED")
        self.assertNotIn("SYNTHETIC_PRIVATE", str(result))

    def test_failed_capability_is_not_retried_for_sync_in_the_same_pass(self):
        self.peer().finalize_unknown = True
        self.fixture.execute()
        self.peer().capability = False
        self.due()
        before = self.peer().calls.count("handshake")
        self.worker.run_once()
        self.assertEqual(self.peer().calls.count("handshake") - before, 1)
        self.assertEqual(self.fixture.operation()["state"], "COMMITTED")

    def test_prepared_reservation_waits_for_fresh_user_instead_of_background_commit(self):
        self.peer().reserve_unknown = True
        self.fixture.execute()
        self.due()
        before = list(self.peer().calls)
        result = self.worker.run_once()
        self.assertEqual(result["waiting_user"], 1)
        self.assertEqual(self.fixture.operation()["state"], "PREPARED")
        self.assertEqual(self.fixture.center.active_snapshot(ENV, GROUP, USER), [])
        self.assertEqual(self.peer().calls, before)
        self.assertIsNotNone(self.scheduled())

    def test_aborted_reservation_is_invalidated_without_mirror_delivery(self):
        self.peer().reserve_unknown = True
        self.fixture.execute()
        operation = self.fixture.operation()
        self.fixture.center.abort(environment_id=ENV, group_id=GROUP, operation_id=operation["operation_id"])
        self.due()
        result = self.worker.run_once()
        self.assertEqual(result["token_confirmed"], 1)
        self.assertEqual(result["delivered"], 0)
        self.assertEqual(self.fixture.operation()["state"], "ABORTED")
        self.assertEqual(self.fixture.operation()["followup"]["token_finalize"]["state"], "INVALIDATED")

    def test_lost_ack_does_not_rebind_and_next_pass_can_settle_original_operation(self):
        self.peer().finalize_unknown = True
        self.fixture.execute()
        self.peer().finalize_unknown = False
        self.due()
        original = self.fixture.center.acknowledge
        def lost(**values):
            if values.get("token_state"):
                raise bindings.BindingError("RESULT_UNKNOWN", "synthetic ACK not stored")
            return original(**values)
        with patch.object(self.fixture.center, "acknowledge", side_effect=lost):
            self.worker.run_once()
        self.assertEqual(self.fixture.operation()["state"], "COMMITTED")
        self.due()
        self.worker.run_once()
        self.assertEqual(self.fixture.operation()["result_code"], "OK")
        self.assertEqual(self.peer().calls.count("reserve"), 1)
        self.assertEqual(self.fixture.center.active_snapshot(ENV, GROUP, USER)[0]["revision"], "1")

    def test_inner_service_and_outer_worker_do_not_double_advance_same_backoff(self):
        self.peer().finalize_unknown = True
        self.fixture.execute()
        self.due()
        before = int(self.fixture.operation()["followup"].get("attempts", 0))
        self.worker.run_once()
        after = int(self.fixture.operation()["followup"]["attempts"])
        self.assertEqual(after, before + 1)
        self.assertEqual(self.fixture.operation()["state"], "COMMITTED")

    def test_restart_new_worker_uses_durable_operation_and_same_process_gate(self):
        self.fixture.execute()
        self.due()
        gate = Lock()
        restarted = recovery_module.OfficialFollowupRecovery(self.fixture.host, gate=gate)
        gate.acquire()
        try:
            before = self.fixture.opens
            self.assertEqual(restarted.run_once()["state"], "busy")
            self.assertEqual(self.fixture.opens, before)
        finally:
            gate.release()
        self.assertEqual(restarted.run_once()["delivered"], 1)

    def test_onebot_selection_never_loads_official_settings(self):
        self.fixture.current = {"platform": "onebot"}
        self.assertEqual(self.worker.run_once()["state"], "not_ready")
        self.assertEqual(self.fixture.opens, 0)

    def test_stopped_or_rotated_context_cannot_create_a_new_peer(self):
        context = self.fixture.host.recovery()
        self.fixture.unchanged = False
        budget = recovery_module.RecoveryBudget()
        with self.assertRaises(bindings.BindingError):
            context.peer(GROUP, self.fixture.group["servers"][0]["server_id"], budget)
        self.assertEqual(self.fixture.peers, {})

    def test_disabled_group_keeps_existing_commit_and_does_not_send(self):
        self.fixture.execute()
        self.due()
        before = list(self.peer().calls)
        self.fixture.update({"enabled": False})
        self.worker.run_once()
        self.assertEqual(self.fixture.operation()["state"], "COMMITTED")
        self.assertEqual(self.peer().calls, before)

    def test_request_budget_stops_before_downlink_and_leaves_durable_pending(self):
        self.fixture.execute()
        self.due()
        result = self.worker.run_once(requests=1)
        self.assertTrue(result["budget_exhausted"])
        self.assertEqual(result["delivered"], 0)
        self.assertNotIn("sync", self.peer().calls)
        self.assertEqual(self.fixture.operation()["result_code"], "SYNC_PENDING")

    def test_expired_pass_budget_does_not_start_network(self):
        self.fixture.execute()
        self.due()
        calls = [0]
        def clock():
            calls[0] += 1
            return 0 if calls[0] == 1 else 30
        worker = recovery_module.OfficialFollowupRecovery(self.fixture.host, clock=clock)
        before = list(self.peer().calls)
        result = worker.run_once()
        self.assertTrue(result["budget_exhausted"])
        self.assertEqual(self.peer().calls, before)

    def test_terminal_guard_explicitly_rejects_business_commit(self):
        self.fixture.execute()
        context = self.fixture.host.recovery()
        operation = self.fixture.operation()
        guard = self.worker._terminal_guard(context, operation)
        scope = transport.BindingScope(ENV, GROUP, operation["original_server_id"])
        with self.assertRaises(bindings.BindingError):
            with guard(scope, dict(operation, policy_phase="commit")): pass


class RecoveryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_starts_one_loop_and_stops_it_on_close(self):
        fixture = prior.RuntimeTests("test_setup_code_creates_group_and_help_only_before_activation")
        fixture.setUp()
        self.addCleanup(fixture.temp.cleanup)
        self.addCleanup(fixture.store.close)
        runtime = fixture.make_runtime()
        self.addCleanup(runtime._original_store.close)
        loop, ran = asyncio.get_running_loop(), asyncio.Event()
        calls = []
        def run_once():
            calls.append("pass")
            loop.call_soon_threadsafe(ran.set)
            return {"state": "ready"}
        runtime.recovery = SimpleNamespace(run_once=run_once)
        runtime.start_recovery()
        task = runtime.recovery_task
        runtime.start_recovery()
        self.assertIs(task, runtime.recovery_task)
        try:
            await asyncio.wait_for(ran.wait(), timeout=2)
        finally:
            runtime.close()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(calls, ["pass"])
        self.assertTrue(task.done())

    async def test_context_owner_preserves_gate_across_runtime_replacement(self):
        with tempfile_directory() as directory:
            activation = SimpleNamespace(status=lambda: {"state": "active", "selection": prior.SELECTION})
            owner = SimpleNamespace()
            module = prior.runtime_module
            with patch.object(module, "_runtime", None):
                first = module.configure(directory, activation, owner=owner)
                second = module.configure(directory, activation, owner=owner)
                self.assertTrue(first.closed)
                self.assertIs(first.recovery.gate, second.recovery.gate)
                second.close()


def tempfile_directory():
    # Kept explicit to make the fresh-directory boundary visible in audit.
    import tempfile
    return tempfile.TemporaryDirectory(prefix="official-recovery-lifecycle-synthetic-")
