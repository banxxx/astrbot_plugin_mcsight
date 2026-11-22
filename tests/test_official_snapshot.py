"""One synthetic SQLite transaction; no configured DB, plugin or HTTP runtime."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import test_official_bindings as prior
import test_official_center as adapter

b = prior.bindings_module


class MirrorSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="official-snapshot-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "new-synthetic.sqlite3"
        with self.db() as connection:
            connection.executescript(prior.SCHEMA)
        self.opens, self.trace = 0, []
        def factory():
            self.opens += 1
            connection = sqlite3.connect(self.path)
            connection.set_trace_callback(self.trace.append)
            return connection
        self.store = b.OfficialBindings(factory, placeholder="?")

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def bind(self, operation=prior.OP_A, game="Player_A", generation=prior.TOKEN_A, suffix="a", user=prior.USER_A):
        self.store.prepare_bind(operation_id=operation, request_key=suffix * 64, environment_id=prior.ENV,
            group_id=prior.GROUP, actor_kind="qq_member", actor_id=user, user_id=user,
            server_id=prior.SERVER, token_generation=generation, game_id=game, policy_revision="1",
            target_server_ids=[prior.SERVER], authorization=dict(scope="self", policy_revision="1"))
        self.store.reserve(environment_id=prior.ENV, group_id=prior.GROUP, operation_id=operation,
            token_generation=generation, reservation_id=prior.RES_A)
        return self.store.commit_bind(environment_id=prior.ENV, group_id=prior.GROUP, operation_id=operation, policy_revision="1")

    def unbind(self, *keys):
        self.store.prepare_unbind(operation_id=prior.OP_C, request_key="c" * 64, environment_id=prior.ENV,
            group_id=prior.GROUP, actor_kind="qq_member", actor_id=prior.USER_A, target_user_id=prior.USER_A,
            kind="unbind_game" if len(keys) == 1 else "unbind_user", targets=[dict(game_key=key, expected_revision="1") for key in keys],
            policy_revision="1", authorization=dict(scope="self", policy_revision="1"), target_server_ids=[prior.SERVER])
        self.store.commit_unbind(environment_id=prior.ENV, group_id=prior.GROUP, operation_id=prior.OP_C, policy_revision="1")

    def code(self, expected, action):
        with self.assertRaises(b.BindingError) as error:
            action()
        self.assertEqual(error.exception.code, expected)

    def test_snapshot_retains_tombstone_user_identity_and_latest_fixed_operation(self):
        self.bind()
        self.bind(prior.OP_B, "Player_B", prior.TOKEN_B, "b")
        self.unbind("player_a")
        rows = self.store.mirror_snapshot(prior.ENV, prior.GROUP)
        self.assertEqual([(r["game_key"], r["state"], r["revision"]) for r in rows],
                         [("player_a", "revoked", "2"), ("player_b", "active", "1")])
        self.assertTrue(all(r["user_id"] == prior.USER_A for r in rows))
        self.assertEqual(rows[0]["last_operation_id"], prior.OP_C)

    def test_rows_and_operation_references_share_one_transaction_and_no_old_table(self):
        self.bind()
        self.bind(prior.OP_B, "Player_B", prior.TOKEN_B, "b")
        self.unbind("player_a", "player_b")
        self.opens, self.trace = 0, []
        rows = self.store.mirror_snapshot(prior.ENV, prior.GROUP)
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.opens, 1)
        self.assertEqual(sum("FROM bindings_qqofficial_ops" in sql for sql in self.trace), 1)
        self.assertTrue(any(sql == "BEGIN IMMEDIATE" for sql in self.trace))
        self.assertFalse(any("FROM bindings " in sql or sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql in self.trace))

    def test_empty_snapshot_is_empty_and_does_not_create_any_operation(self):
        self.assertEqual(self.store.mirror_snapshot(prior.ENV, prior.GROUP), [])
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM bindings_qqofficial_ops").fetchone()[0], 0)

    def test_capacity_refuses_partial_snapshot_and_invalid_limits_before_open(self):
        self.bind()
        self.bind(prior.OP_B, "Player_B", prior.TOKEN_B, "b")
        self.code("LIMIT_EXCEEDED", lambda: self.store.mirror_snapshot(prior.ENV, prior.GROUP, max_rows=1))
        opened = self.opens
        for value in (True, 0, 10001):
            self.code("INVALID_INPUT", lambda: self.store.mirror_snapshot(prior.ENV, prior.GROUP, max_rows=value))
        self.assertEqual(self.opens, opened)

    def test_missing_last_operation_is_storage_error_not_an_empty_snapshot(self):
        self.bind()
        with self.db() as connection:
            connection.execute("DELETE FROM bindings_qqofficial_ops")
        self.code("STORAGE_ERROR", lambda: self.store.mirror_snapshot(prior.ENV, prior.GROUP))

    def test_row_effect_user_state_or_revision_mismatch_fails_whole_snapshot(self):
        self.bind()
        for column, value in (("qq", "SYNTHETIC_OTHER_USER"), ("state", "revoked"), ("revision", 2)):
            with self.db() as connection:
                connection.execute(f"UPDATE bindings_qqofficial SET {column}=?", (value,))
            self.code("STORAGE_ERROR", lambda: self.store.mirror_snapshot(prior.ENV, prior.GROUP))
            with self.db() as connection:
                connection.execute("UPDATE bindings_qqofficial SET qq=?,state='active',revision=1", (prior.USER_A,))

    def test_corrupt_operation_hash_is_not_accepted_as_confirmation(self):
        self.bind()
        with self.db() as connection:
            connection.execute("UPDATE bindings_qqofficial_ops SET payload_hash=?", ("0" * 64,))
        self.code("STORAGE_ERROR", lambda: self.store.mirror_snapshot(prior.ENV, prior.GROUP))

    def test_foreign_operation_reference_is_rejected_even_if_its_effect_looks_identical(self):
        self.bind()
        with self.db() as connection:
            row = connection.execute("SELECT intent FROM bindings_qqofficial_ops").fetchone()[0]
            canonical = dict(environment_id=prior.ENV, group_id="SYNTHETIC_OTHER_GROUP", actor_kind="qq_member", actor_id=prior.USER_A,
                user_id=prior.USER_A, server_id=prior.SERVER, token_generation=prior.TOKEN_A, policy_revision="1", kind="bind", intent=json.loads(row), nickname_basis=None)
            connection.execute("UPDATE bindings_qqofficial_ops SET group_id=?,payload_hash=?", (canonical["group_id"], b.payload_hash(canonical)))
        self.code("STORAGE_ERROR", lambda: self.store.mirror_snapshot(prior.ENV, prior.GROUP))

    def test_wrong_source_from_nonbinary_database_comparison_is_rejected(self):
        row = dict(environment_id=prior.ENV, group_id="synthetic_group_a", game_id="Player_A", game_key="player_a",
                   qq=prior.USER_A, state="active", revision=1, last_operation_id=prior.OP_A)
        class Cursor(adapter.Cursor):
            def execute(self, sql, values=()):
                super().execute(sql, values)
                if "FROM bindings_qqofficial WHERE" in sql:
                    self.rows = [row]
        store = b.OfficialBindings(lambda: adapter.Connection(Cursor()), placeholder="?")
        self.code("STORAGE_ERROR", lambda: store.mirror_snapshot(prior.ENV, prior.GROUP))

    def test_shared_snapshot_fixture_verifies_against_python_transaction_model(self):
        fixture = adapter.FIXTURE["mirror_snapshot"]
        with self.db() as connection:
            for operation in fixture["operations"]:
                fields = list(operation)
                values = [json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for value in operation.values()]
                connection.execute("INSERT INTO bindings_qqofficial_ops (" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields) + ")", values)
            for row in fixture["rows"]:
                connection.execute("INSERT INTO bindings_qqofficial (environment_id,group_id,game_key,game_id,qq,state,revision,last_operation_id,bind_time,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (prior.ENV, prior.GROUP, row["game_key"], row["game_id"], row["user_id"], row["state"], row["revision"], row["last_operation_id"], "2026-11-18 00:00:00.000000", "2026-11-18 00:00:00.000000"))
        self.assertEqual(self.store.mirror_snapshot(prior.ENV, prior.GROUP), fixture["rows"])


if __name__ == "__main__":
    unittest.main()
