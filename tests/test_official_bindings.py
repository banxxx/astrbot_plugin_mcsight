"""Synthetic SQLite transaction tests; no configured or live DB is opened."""
import sqlite3
import json
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import test_official_readonly as prior

core = prior.core
bindings_module = prior.module("official_bindings")

ENV = prior.ENV
GROUP = prior.GROUP
USER_A = prior.USER
USER_B = "SYNTHETIC_USER_B"
SERVER = "33333333-3333-4333-8333-333333333333"
TOKEN_A = "44444444-4444-4444-8444-444444444444"
TOKEN_B = "55555555-5555-4555-8555-555555555555"
OP_A = "66666666-6666-4666-8666-666666666666"
OP_B = "77777777-7777-4777-8777-777777777777"
OP_C = "88888888-8888-4888-8888-888888888888"
RES_A = "99999999-9999-4999-8999-999999999999"
RES_B = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


SCHEMA = """
CREATE TABLE bindings_qqofficial (
 id INTEGER PRIMARY KEY AUTOINCREMENT, environment_id TEXT NOT NULL, group_id TEXT NOT NULL,
 game_key TEXT NOT NULL, game_id TEXT NOT NULL, qq TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL,
 last_operation_id TEXT NOT NULL, bind_time TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(environment_id,group_id,game_key));
CREATE TABLE bindings_qqofficial_ops (
 operation_id TEXT PRIMARY KEY, environment_id TEXT NOT NULL, group_id TEXT NOT NULL, request_key TEXT NOT NULL,
 payload_hash TEXT NOT NULL, kind TEXT NOT NULL, actor_kind TEXT NOT NULL, actor_id TEXT NOT NULL, target_user_id TEXT NOT NULL,
 original_server_id TEXT, token_generation TEXT, reservation_id TEXT, policy_revision INTEGER NOT NULL, intent TEXT NOT NULL,
 nickname_basis TEXT, state TEXT NOT NULL, result_code TEXT, effects TEXT NOT NULL, followup TEXT NOT NULL,
 next_attempt_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(environment_id,request_key),
 UNIQUE(environment_id,original_server_id,token_generation));
"""


class CenterTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="official-bindings-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "center.sqlite3"
        with self.db() as connection:
            connection.executescript(SCHEMA)
        self.store = bindings_module.OfficialBindings(lambda: sqlite3.connect(self.path), placeholder="?")

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

    def prepare(self, *, operation=OP_A, token=TOKEN_A, user=USER_A, game="Player_A", suffix="A", basis=None, servers=None):
        return self.store.prepare_bind(operation_id=operation, request_key=(suffix.lower() * 64)[:64],
            environment_id=ENV, group_id=GROUP,
            actor_kind="qq_member", actor_id=user, user_id=user, server_id=SERVER,
            token_generation=token, game_id=game, policy_revision="1", target_server_ids=servers or [SERVER],
            authorization={"scope": "self", "policy_revision": "1"}, nickname_basis=basis)

    def reserve_and_commit(self, **values):
        reservation = values.pop("reservation", RES_A)
        token = values.get("token", TOKEN_A)
        operation = self.prepare(**values)
        self.store.reserve(environment_id=ENV, group_id=GROUP, operation_id=operation["operation_id"],
                           reservation_id=reservation, token_generation=token)
        return self.store.commit_bind(environment_id=ENV, group_id=GROUP, policy_revision="1", operation_id=operation["operation_id"])

    def test_prepare_is_idempotent_but_rejects_same_key_with_different_payload(self):
        first = self.prepare()
        duplicate = self.prepare()
        self.assertEqual(first["operation_id"], duplicate["operation_id"])
        with self.assertRaises(core.OfficialError):
            self.prepare(operation=OP_B, game="Player_B", suffix="A")
        self.assertEqual(self.store.get_operation(ENV, OP_A, group_id=GROUP)["state"], "PREPARED")

    def test_bind_requires_durable_reservation_and_commits_binding_and_outbox_together(self):
        self.prepare()
        with self.assertRaises(core.OfficialError):
            self.store.commit_bind(environment_id=ENV, group_id=GROUP, policy_revision="1", operation_id=OP_A)
        self.store.reserve(environment_id=ENV, group_id=GROUP, operation_id=OP_A, reservation_id=RES_A, token_generation=TOKEN_A)
        reserved = self.store.reserve(environment_id=ENV, group_id=GROUP, operation_id=OP_A, reservation_id=RES_A, token_generation=TOKEN_A)
        self.assertEqual(reserved["state"], "RESERVED")
        committed = self.store.commit_bind(environment_id=ENV, group_id=GROUP, policy_revision="1", operation_id=OP_A)
        self.assertEqual((committed["state"], committed["effects"][0]["revision"]), ("COMMITTED", "1"))
        self.assertEqual(committed["followup"]["token_finalize"]["decision"], "COMMITTED")
        self.assertEqual(committed["followup"]["delivery"][SERVER]["mirror"], "PENDING")
        self.assertEqual(self.store.commit_bind(environment_id=ENV, group_id=GROUP, policy_revision="1", operation_id=OP_A), committed)
        with self.db() as connection:
            row = connection.execute("SELECT qq,state,revision,last_operation_id FROM bindings_qqofficial").fetchone()
        self.assertEqual(row, (USER_A, "active", 1, OP_A))

    def test_one_user_can_bind_multiple_accounts_but_an_account_has_one_active_owner(self):
        first = self.reserve_and_commit()
        second = self.reserve_and_commit(operation=OP_B, token=TOKEN_B, game="Player_B", suffix="B", reservation=RES_B)
        self.assertEqual([first["state"], second["state"]], ["COMMITTED", "COMMITTED"])
        self.assertEqual(len(second["followup"]["delivery"]), 1)
        occupied = self.reserve_and_commit(operation=OP_C, token="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            reservation="cccccccc-cccc-4ccc-8ccc-cccccccccccc", user=USER_B, game="Player_A", suffix="C")
        self.assertEqual((occupied["state"], occupied["result_code"]), ("REJECTED", "CONFLICT"))
        with self.db() as connection:
            rows = connection.execute("SELECT game_key,qq,state,revision FROM bindings_qqofficial ORDER BY game_key").fetchall()
        self.assertEqual(rows, [("player_a", USER_A, "active", 1), ("player_b", USER_A, "active", 1)])

    def test_revoked_binding_reactivation_increments_revision(self):
        with self.db() as connection:
            connection.execute("INSERT INTO bindings_qqofficial VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",
                (ENV,GROUP,"player_a","Player_A",USER_B,"revoked",7,OP_B,"2026-11-01","2026-11-01"))
        result = self.reserve_and_commit()
        self.assertEqual(result["effects"][0]["revision"], "8")

    def test_changed_nickname_basis_rejects_and_schedules_reservation_abort(self):
        self.reserve_and_commit(game="Basis_A", suffix="B")
        operation = self.prepare(operation=OP_B, token=TOKEN_B, game="Player_B", suffix="C",
                                 basis={"game_key": "basis_a", "expected_revision": "1"})
        self.store.reserve(environment_id=ENV, group_id=GROUP, operation_id=OP_B, reservation_id=RES_B, token_generation=TOKEN_B)
        with self.db() as connection:
            connection.execute("UPDATE bindings_qqofficial SET revision=2 WHERE game_key='basis_a'")
        result = self.store.commit_bind(environment_id=ENV, group_id=GROUP, policy_revision="1", operation_id=OP_B)
        self.assertEqual((result["state"], result["result_code"]), ("REJECTED", "CONFLICT"))
        self.assertEqual(result["followup"]["token_finalize"]["decision"], "ABORTED")
        with self.db() as connection:
            count = connection.execute("SELECT COUNT(*) FROM bindings_qqofficial WHERE game_key='player_b'").fetchone()[0]
        self.assertEqual(count, 0)

    def test_failure_between_binding_write_and_operation_commit_rolls_back_both_tables(self):
        self.prepare()
        self.store.reserve(environment_id=ENV, group_id=GROUP, operation_id=OP_A, reservation_id=RES_A, token_generation=TOKEN_A)
        with self.db() as connection:
            connection.execute("CREATE TRIGGER reject_operation_commit BEFORE UPDATE OF state ON bindings_qqofficial_ops"
                               " WHEN NEW.state='COMMITTED' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        with self.assertRaises(core.OfficialError):
            self.store.commit_bind(environment_id=ENV, group_id=GROUP, policy_revision="1", operation_id=OP_A)
        with self.db() as connection:
            binding_count = connection.execute("SELECT COUNT(*) FROM bindings_qqofficial").fetchone()[0]
            op_state = connection.execute("SELECT state FROM bindings_qqofficial_ops WHERE operation_id=?", (OP_A,)).fetchone()[0]
        self.assertEqual(binding_count, 0)
        self.assertEqual(op_state, "RESERVED")

    def unbind(self, targets, *, operation=OP_C, user=USER_A, commit=True):
        result = self.store.prepare_unbind(operation_id=operation, request_key="d" * 64,
            environment_id=ENV, group_id=GROUP, actor_kind="qq_member", actor_id=user, target_user_id=user,
            kind="unbind_user" if len(targets) > 1 else "unbind_game", targets=targets,
            policy_revision="1", authorization={"scope": "self", "policy_revision": "1"},
            target_server_ids=[SERVER])
        return self.store.commit_unbind(environment_id=ENV, group_id=GROUP, operation_id=operation,
                                       policy_revision="1") if commit else result

    def test_unbind_one_account_keeps_other_account_and_tombstone(self):
        self.reserve_and_commit()
        self.reserve_and_commit(operation=OP_B, token=TOKEN_B, game="Player_B", suffix="B")
        result = self.unbind([{"game_key": "player_a", "expected_revision": "1"}])
        self.assertEqual(result["state"], "COMMITTED")
        self.assertEqual(result["effects"], [{"game_key": "player_a", "game_id": "Player_A",
            "user_id": USER_A, "state": "revoked", "revision": "2"}])
        with self.db() as connection:
            rows = connection.execute("SELECT game_key,state,revision FROM bindings_qqofficial ORDER BY game_key").fetchall()
        self.assertEqual(rows, [("player_a", "revoked", 2), ("player_b", "active", 1)])
        again = self.store.commit_unbind(environment_id=ENV, group_id=GROUP, operation_id=OP_C, policy_revision="1")
        self.assertEqual(result, again)

    def test_stale_batch_target_rejects_entire_set_before_revoking_any_account(self):
        self.reserve_and_commit()
        self.reserve_and_commit(operation=OP_B, token=TOKEN_B, game="Player_B", suffix="B")
        result = self.unbind([{"game_key": "player_a", "expected_revision": "1"},
                              {"game_key": "player_b", "expected_revision": "2"}])
        self.assertEqual((result["state"], result["result_code"]), ("REJECTED", "CONFLICT"))
        with self.db() as connection:
            states = connection.execute("SELECT state,revision FROM bindings_qqofficial").fetchall()
        self.assertEqual(states, [("active", 1), ("active", 1)])

    def test_batch_fault_rolls_back_all_revocations_and_preserves_prepared_operation(self):
        self.reserve_and_commit()
        self.reserve_and_commit(operation=OP_B, token=TOKEN_B, game="Player_B", suffix="B")
        self.unbind([{"game_key": "player_a", "expected_revision": "1"},
                     {"game_key": "player_b", "expected_revision": "1"}], commit=False)
        with self.db() as connection:
            connection.execute("CREATE TRIGGER reject_second_revoke BEFORE UPDATE OF state ON bindings_qqofficial"
                               " WHEN NEW.game_key='player_b' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        with self.assertRaises(core.OfficialError):
            self.store.commit_unbind(environment_id=ENV, group_id=GROUP, operation_id=OP_C, policy_revision="1")
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT state,revision FROM bindings_qqofficial").fetchall(),
                             [("active", 1), ("active", 1)])
        self.assertEqual(self.store.get_operation(ENV, OP_C, group_id=GROUP)["state"], "PREPARED")

    def test_unbind_fixed_set_never_expands_to_new_account(self):
        self.reserve_and_commit()
        self.unbind([{"game_key": "player_a", "expected_revision": "1"}], commit=False)
        self.reserve_and_commit(operation=OP_B, token=TOKEN_B, game="Player_B", suffix="B")
        result = self.store.commit_unbind(environment_id=ENV, group_id=GROUP, operation_id=OP_C, policy_revision="1")
        self.assertEqual([e["game_key"] for e in result["effects"]], ["player_a"])
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT state FROM bindings_qqofficial WHERE game_key='player_b'").fetchone()[0], "active")

    def test_owner_change_cannot_be_revoked_by_stale_self_request(self):
        self.reserve_and_commit()
        self.unbind([{"game_key": "player_a", "expected_revision": "1"}], commit=False)
        with self.db() as connection:
            connection.execute("UPDATE bindings_qqofficial SET qq=?,revision=3", (USER_B,))
        result = self.store.commit_unbind(environment_id=ENV, group_id=GROUP, operation_id=OP_C, policy_revision="1")
        self.assertEqual(result["state"], "REJECTED")
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT qq,state,revision FROM bindings_qqofficial").fetchone(), (USER_B, "active", 3))

    def test_commit_ack_loss_recovers_committed_operation_without_rebinding(self):
        self.prepare()
        self.store.reserve(environment_id=ENV, group_id=GROUP, operation_id=OP_A, reservation_id=RES_A, token_generation=TOKEN_A)
        path = self.path
        class LostAck:
            def __init__(self): self.connection = sqlite3.connect(path)
            def cursor(self): return self.connection.cursor()
            def commit(self):
                self.connection.commit()
                raise TimeoutError("SYNTHETIC_PRIVATE_ERROR")
            def rollback(self):
                self.connection.rollback()
                raise RuntimeError("SYNTHETIC_PRIVATE_ERROR")
            def close(self): self.connection.close()
        unknown = bindings_module.OfficialBindings(LostAck, placeholder="?")
        with self.assertRaises(bindings_module.BindingError) as caught:
            unknown.commit_bind(environment_id=ENV, group_id=GROUP, operation_id=OP_A, policy_revision="1")
        self.assertEqual(caught.exception.code, "RESULT_UNKNOWN")
        self.assertNotIn("SYNTHETIC_PRIVATE", str(caught.exception))
        restarted = bindings_module.OfficialBindings(lambda: sqlite3.connect(self.path), placeholder="?")
        result = restarted.get_operation(ENV, OP_A, group_id=GROUP)
        self.assertEqual(result["state"], "COMMITTED")
        self.assertEqual(restarted.commit_bind(environment_id=ENV, group_id=GROUP, operation_id=OP_A, policy_revision="1"), result)
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*),MAX(revision) FROM bindings_qqofficial").fetchone(), (1, 1))

    def test_abort_and_commit_race_has_one_terminal_outcome(self):
        self.prepare()
        self.store.reserve(environment_id=ENV, group_id=GROUP, operation_id=OP_A, reservation_id=RES_A, token_generation=TOKEN_A)
        barrier = Barrier(2)
        def commit():
            barrier.wait()
            return self.store.commit_bind(environment_id=ENV, group_id=GROUP, operation_id=OP_A, policy_revision="1")
        def abort():
            barrier.wait()
            return self.store.abort(environment_id=ENV, group_id=GROUP, operation_id=OP_A)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(commit), pool.submit(abort)]
            outcomes = [future.result() for future in futures]
        self.assertEqual(outcomes[0]["state"], outcomes[1]["state"])
        with self.db() as connection:
            count = connection.execute("SELECT COUNT(*) FROM bindings_qqofficial").fetchone()[0]
        self.assertEqual(count, int(outcomes[0]["state"] == "COMMITTED"))

    def test_prepared_abort_keeps_unknown_reservation_recovery_pending(self):
        self.prepare()
        result = self.store.abort(environment_id=ENV, group_id=GROUP, operation_id=OP_A)
        self.assertEqual(result["state"], "ABORTED")
        self.assertIsNone(result["followup"]["token_finalize"]["reservation_id"])
        with self.assertRaises(bindings_module.BindingError):
            self.prepare(operation=OP_B, suffix="B")
        result = self.store.commit_bind(environment_id=ENV, group_id=GROUP, operation_id=OP_A, policy_revision="1")
        self.assertEqual(result["state"], "ABORTED")

    def test_payload_hash_binds_owner_generation_policy_and_scope(self):
        self.prepare()
        for changed in ({"user": USER_B}, {"token": TOKEN_B}, {"game": "Player_B"},
                        {"basis": {"game_key": "basis_a", "expected_revision": "1"}}):
            with self.subTest(changed=changed), self.assertRaises(bindings_module.BindingError):
                self.prepare(**changed)
        # A new event with an identical intent and generation recovers the original ID.
        self.assertEqual(self.prepare(operation=OP_B, suffix="B")["operation_id"], OP_A)
        self.assertIsNone(self.store.get_operation(ENV, OP_A, group_id="SYNTHETIC_OTHER_GROUP"))
        with self.assertRaises(bindings_module.BindingError):
            self.store.abort(environment_id=ENV, group_id="SYNTHETIC_OTHER_GROUP", operation_id=OP_A)

    def test_changed_policy_revision_rejects_before_any_binding_write(self):
        self.prepare()
        self.store.reserve(environment_id=ENV, group_id=GROUP, operation_id=OP_A, reservation_id=RES_A, token_generation=TOKEN_A)
        result = self.store.commit_bind(environment_id=ENV, group_id=GROUP, operation_id=OP_A, policy_revision="2")
        self.assertEqual((result["state"], result["result_code"]), ("REJECTED", "CONFLICT"))

    def test_center_unavailable_never_creates_fallback_data(self):
        def unavailable(): raise OSError("SYNTHETIC_PRIVATE_ENDPOINT")
        store = bindings_module.OfficialBindings(unavailable)
        with self.assertRaises(bindings_module.BindingError) as caught:
            store.abort(environment_id=ENV, group_id=GROUP, operation_id=OP_A)
        self.assertEqual(caught.exception.code, "DB_UNAVAILABLE")
        self.assertNotIn("SYNTHETIC_PRIVATE_ENDPOINT", str(caught.exception))
        self.assertEqual([p.name for p in Path(self.temp.name).iterdir()], ["center.sqlite3"])

    @staticmethod
    def ack(key="player_a", revision="1", effective="1", status="APPLIED"):
        return {"game_key": key, "result": "applied" if revision == effective else "ignored",
                "requested_revision": revision, "effective_revision": effective, "persisted": True,
                "whitelist": status, "online": status}

    def test_concurrent_target_ack_merge_and_token_finalization_clear_pending_work(self):
        server_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        self.reserve_and_commit(servers=[SERVER, server_b])
        barrier = Barrier(2)
        def acknowledge(server):
            barrier.wait()
            return self.store.acknowledge(environment_id=ENV, group_id=GROUP, operation_id=OP_A,
                                          server_id=server, results=[self.ack()])
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(acknowledge, server) for server in (SERVER, server_b)]
            for future in futures: future.result()
        op = self.store.get_operation(ENV, OP_A, group_id=GROUP)
        self.assertEqual([t["mirror"] for t in op["followup"]["delivery"].values()], ["APPLIED", "APPLIED"])
        self.assertEqual(op["result_code"], "SYNC_PENDING")
        final = self.store.acknowledge(environment_id=ENV, group_id=GROUP, operation_id=OP_A, server_id=SERVER,
                                      token_state="CONSUMED", reservation_id=RES_A, token_generation=TOKEN_A)
        self.assertEqual(final["result_code"], "OK")
        self.assertEqual(self.store.due_operations(ENV), [])

    def test_batch_partial_ack_is_never_aggregated_as_complete(self):
        self.reserve_and_commit()
        self.reserve_and_commit(operation=OP_B, token=TOKEN_B, game="Player_B", suffix="B")
        self.unbind([{"game_key": "player_a", "expected_revision": "1"},
                     {"game_key": "player_b", "expected_revision": "1"}])
        first = self.store.acknowledge(environment_id=ENV, group_id=GROUP, operation_id=OP_C, server_id=SERVER,
                                      results=[self.ack(revision="2", effective="2")])
        self.assertEqual(first["followup"]["delivery"][SERVER]["mirror"], "PENDING")
        second = self.store.acknowledge(environment_id=ENV, group_id=GROUP, operation_id=OP_C, server_id=SERVER,
                                       results=[self.ack(key="player_b", revision="2", effective="2")])
        self.assertEqual(second["result_code"], "OK")

    def test_old_ack_cannot_regress_newer_revision_or_falsely_mark_old_actions_applied(self):
        self.reserve_and_commit()
        result = self.store.acknowledge(environment_id=ENV, group_id=GROUP, operation_id=OP_A, server_id=SERVER,
                                       results=[self.ack(effective="2", status="SUPERSEDED")])
        original = result["followup"]["delivery"][SERVER]
        result = self.store.acknowledge(environment_id=ENV, group_id=GROUP, operation_id=OP_A, server_id=SERVER,
                                       results=[self.ack()])
        self.assertEqual(result["followup"]["delivery"][SERVER], original)
        with self.assertRaises(bindings_module.BindingError):
            self.store.acknowledge(environment_id=ENV, group_id=GROUP, operation_id=OP_A, server_id=SERVER,
                                   results=[self.ack(effective="3", status="APPLIED")])
        with self.assertRaises(bindings_module.BindingError):
            self.store.acknowledge(environment_id=ENV, group_id=GROUP, operation_id=OP_A, server_id=SERVER,
                                   results=[self.ack(revision="2", effective="2")])

    def test_pending_query_is_environment_scoped_and_defer_is_bounded(self):
        self.prepare()
        self.assertEqual(self.store.due_operations(ENV), [])
        self.store.reserve(environment_id=ENV, group_id=GROUP, operation_id=OP_A,
                           reservation_id=RES_A, token_generation=TOKEN_A)
        self.store.commit_bind(environment_id=ENV, group_id=GROUP, operation_id=OP_A, policy_revision="1")
        self.assertEqual([v["operation_id"] for v in self.store.due_operations(ENV)], [OP_A])
        self.assertEqual(self.store.due_operations(prior.OTHER_ENV), [])
        result = self.store.defer(environment_id=ENV, group_id=GROUP, operation_id=OP_A)
        self.assertEqual(result["followup"]["attempts"], "1")
        self.assertEqual(self.store.due_operations(ENV), [])
        with self.assertRaises(bindings_module.BindingError): self.store.due_operations(ENV, limit=0)
        with self.assertRaises(bindings_module.BindingError):
            self.store.defer(environment_id=ENV, group_id=GROUP, operation_id="77777777-7777-4777-8777-777777777777")

    def test_oversized_outbox_is_rejected_before_creating_operation(self):
        targets = [{"game_key": f"player_{i}", "expected_revision": "1"} for i in range(500)]
        servers = [str(uuid4()) for _ in range(100)]
        with self.assertRaises(bindings_module.BindingError) as caught:
            self.store.prepare_unbind(operation_id=OP_A, request_key="e" * 64,
                environment_id=ENV, group_id=GROUP, actor_kind="qq_member", actor_id=USER_A, target_user_id=USER_A,
                kind="unbind_user", targets=targets, policy_revision="1",
                authorization={"scope": "self", "policy_revision": "1"}, target_server_ids=servers)
        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")
        self.assertIsNone(self.store.get_operation(ENV, OP_A, group_id=GROUP))

    def test_token_ack_must_match_generation_and_original_server(self):
        self.reserve_and_commit()
        for server, generation in ((SERVER, TOKEN_B), (TOKEN_B, TOKEN_A)):
            with self.assertRaises(bindings_module.BindingError):
                self.store.acknowledge(environment_id=ENV, group_id=GROUP, operation_id=OP_A, server_id=server,
                    token_state="CONSUMED", reservation_id=RES_A, token_generation=generation)
        self.assertEqual(self.store.get_operation(ENV, OP_A, group_id=GROUP)["followup"]["token_finalize"]["state"], "PENDING")

    def test_pre_reserve_claim_is_atomic_and_only_one_caller_may_send(self):
        self.prepare()
        barrier = Barrier(2)
        def claim():
            barrier.wait()
            return self.store.claim_reservation(environment_id=ENV, group_id=GROUP,
                operation_id=OP_A, token_generation=TOKEN_A)[1]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim), pool.submit(claim)]
            self.assertEqual(sorted(f.result() for f in futures), [False, True])
        self.assertEqual(self.store.due_operations(ENV)[0]["state"], "PREPARED")
        restarted = bindings_module.OfficialBindings(lambda: sqlite3.connect(self.path), placeholder="?")
        self.assertFalse(restarted.claim_reservation(environment_id=ENV, group_id=GROUP,
            operation_id=OP_A, token_generation=TOKEN_A)[1])

    def test_abort_pending_reserve_clears_dispatch_marker_and_preserves_invalidation_work(self):
        self.prepare()
        self.store.mark_reservation_pending(environment_id=ENV, group_id=GROUP, operation_id=OP_A, token_generation=TOKEN_A)
        result = self.store.abort(environment_id=ENV, group_id=GROUP, operation_id=OP_A)
        self.assertEqual(result["state"], "ABORTED")
        self.assertIsNone(result["followup"].get("token_reservation"))
        self.assertEqual(result["followup"]["token_finalize"]["decision"], "ABORTED")
        self.assertEqual(self.store.due_operations(ENV)[0]["state"], "ABORTED")

    def test_corrupt_intent_or_followup_is_not_rewritten_by_recovery(self):
        self.reserve_and_commit()
        with self.db() as connection:
            original = connection.execute("SELECT intent,followup FROM bindings_qqofficial_ops WHERE operation_id=?", (OP_A,)).fetchone()
        intent = json.loads(original[0])
        intent["game_key"] = "other_player"
        broken_followup = json.loads(original[1])
        broken_followup["delivery"][SERVER]["mirror"] = "APPLIED"
        for changed_intent, changed_followup in ((json.dumps(intent), original[1]), (original[0], json.dumps(broken_followup))):
            with self.db() as connection:
                connection.execute("UPDATE bindings_qqofficial_ops SET intent=?,followup=? WHERE operation_id=?",
                    (changed_intent, changed_followup, OP_A))
            with self.assertRaises(bindings_module.BindingError) as caught:
                self.store.defer(environment_id=ENV, group_id=GROUP, operation_id=OP_A)
            self.assertEqual(caught.exception.code, "STORAGE_ERROR")
            with self.db() as connection:
                self.assertEqual(connection.execute("SELECT intent,followup FROM bindings_qqofficial_ops WHERE operation_id=?", (OP_A,)).fetchone(),
                                 (changed_intent, changed_followup))

    def test_same_revision_content_conflict_remains_pending_after_later_ack(self):
        self.reserve_and_commit()
        conflict = dict(self.ack(), result="conflict", whitelist="CONFLICT", online="CONFLICT")
        self.store.acknowledge(environment_id=ENV, group_id=GROUP, operation_id=OP_A, server_id=SERVER, results=[conflict])
        result = self.store.acknowledge(environment_id=ENV, group_id=GROUP, operation_id=OP_A, server_id=SERVER, results=[self.ack()])
        self.assertEqual(result["followup"]["delivery"][SERVER]["mirror"], "CONFLICT")
        self.assertEqual(result["result_code"], "SYNC_PENDING")

    def test_request_lookup_and_active_snapshot_remain_scope_and_case_sensitive(self):
        self.reserve_and_commit()
        self.assertIsNone(self.store.find_request(ENV, GROUP.lower(), "a" * 64))
        self.assertEqual(self.store.find_request(ENV, GROUP, "a" * 64)["operation_id"], OP_A)
        self.assertEqual(self.store.active_snapshot(ENV, GROUP, USER_A.lower()), [])
        self.assertEqual(self.store.active_snapshot(ENV, GROUP, USER_A)[0]["game_id"], "Player_A")
        with self.db() as connection:
            connection.execute("UPDATE bindings_qqofficial SET game_id='DIFFERENT'")
        with self.assertRaises(bindings_module.BindingError) as caught:
            self.store.check_game(ENV, GROUP, "player_a")
        self.assertEqual(caught.exception.code, "STORAGE_ERROR")


if __name__ == "__main__":
    unittest.main()
