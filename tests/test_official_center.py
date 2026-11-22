"""No real MySQL/credentials: shared synthetic metadata and fake DB-API only."""
from copy import deepcopy
import json
from pathlib import Path
import re
import unittest

import test_official_bindings as prior

center = prior.prior.module("official_center")
bindings = prior.bindings_module
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "official_center_v1.json").read_text(encoding="utf-8"))
assert FIXTURE["synthetic"] is True


class Cursor:
    def __init__(self, *, metadata=None, failure=None, operation=None, tuples=False):
        self.metadata = deepcopy(metadata or FIXTURE["metadata"])
        self.failure, self.operation, self.tuples = failure, operation, tuples
        self.statements, self.rows, self.description, self.closed = [], [], [], False
    def execute(self, sql, values=()):
        self.statements.append((sql, values))
        if self.failure and self.failure[0] in sql:
            raise RuntimeError(self.failure[1], "SYNTHETIC_PRIVATE_CONNECTOR_TEXT")
        key = {center.SESSION_SQL: "session", center.TABLE_SQL: "tables",
               center.COLUMN_SQL: "columns", center.INDEX_SQL: "indexes"}.get(sql)
        self.rows = ([deepcopy(self.metadata[key])] if key == "session" else deepcopy(self.metadata[key])) if key else []
        if " WHERE operation_id=" in sql and sql.startswith("SELECT operation_id"):
            self.rows = [deepcopy(self.operation)] if self.operation else []
        if self.tuples and self.rows:
            names = list(self.rows[0])
            self.description = [(name,) for name in names]
            self.rows = [tuple(row[name] for name in names) for row in self.rows]
    def fetchmany(self, count):
        result, self.rows = self.rows[:count], self.rows[count:]
        return result
    def fetchall(self):
        result, self.rows = self.rows, []
        return result
    def fetchone(self):
        values = self.fetchmany(1)
        return values[0] if values else None
    def close(self): self.closed = True


class Connection:
    def __init__(self, cursor=None, *, commit_error=False):
        self.cur = cursor or Cursor()
        self.commits = self.rollbacks = self.begins = 0
        self.closed, self.commit_error = False, commit_error
    def cursor(self): return self.cur
    def begin(self): self.begins += 1
    def commit(self):
        self.commits += 1
        if self.commit_error: raise TimeoutError("SYNTHETIC_PRIVATE_ACK_LOST")
    def rollback(self): self.rollbacks += 1
    def close(self): self.closed = True


def settings():
    return dict(host="db.example.invalid", port=4000, database="synthetic_official",
                user="SYNTHETIC_DB_USER", password="SYNTHETIC_NOT_A_REAL_PASSWORD", ssl_ca="SYNTHETIC_PUBLIC_CA.pem")


class CenterAdapterTests(unittest.TestCase):
    def assertCode(self, code, action):
        with self.assertRaises(bindings.BindingError) as error:
            action()
        self.assertEqual(error.exception.code, code)
        self.assertNotIn("SYNTHETIC_PRIVATE", str(error.exception))

    def test_shared_schema_and_canonical_bytes_match_fixture(self):
        self.assertEqual(center.schema_spec(), FIXTURE["schema_spec"])
        for case in FIXTURE["canonical_cases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(bindings.canonical_intent_json(case["intent"]), case["canonical"])
                self.assertEqual(bindings.payload_hash(case["intent"]), case["payload_hash"])
        codec = FIXTURE["codec"]
        self.assertEqual(bindings.canonical_intent_json(codec["value"]), codec["canonical"])
        self.assertEqual(bindings.payload_hash(codec["value"]), codec["payload_hash"])

    def test_canonical_rejects_numbers_bools_surrogates_and_non_ascii_keys(self):
        for value in ({"revision": 1}, {"revision": True}, {"text": "\ud800"}, {"字段": "synthetic"}):
            with self.subTest(value=repr(value)):
                self.assertCode("INVALID_INPUT", lambda: bindings.payload_hash(value))
        with self.assertRaises(prior.core.OfficialError):
            prior.core.identifier("SYNTHETIC_\udfff")

    def test_all_shared_persisted_operations_pass_original_warehouse_validation(self):
        for operation in FIXTURE["operations"]:
            with self.subTest(operation=operation["name"]):
                bindings.OfficialBindings._validate_operation(deepcopy(operation["row"]))

    def test_schema_rejects_identity_collation_and_unsigned_or_precision_drift(self):
        for field, attribute, wrong in (("group_id", "collation_name", "utf8mb4_general_ci"),
                                        ("revision", "column_type", "bigint"),
                                        ("bind_time", "datetime_precision", 0),
                                        ("qq", "is_nullable", "YES")):
            value = deepcopy(FIXTURE["metadata"])
            next(row for row in value["columns"] if row["table_name"] == center.TABLES[0] and row["column_name"] == field)[attribute] = wrong
            with self.subTest(field=field): self.assertCode("SCHEMA_MISMATCH", lambda: center.validate_schema(**value))

    def test_schema_rejects_missing_table_columns_indexes_and_views(self):
        for change in (lambda m: m["tables"].pop(), lambda m: m["columns"].pop(),
                       lambda m: m["indexes"].pop(), lambda m: m["tables"][0].update(table_type="VIEW")):
            value = deepcopy(FIXTURE["metadata"]); change(value)
            self.assertCode("SCHEMA_MISMATCH", lambda: center.validate_schema(**value))

    def test_schema_rejects_extra_unique_user_index_prefix_and_wrong_key_order(self):
        for change in (
            lambda m: m["indexes"].append(dict(table_name=center.TABLES[0], index_name="bad_unique_user",
                non_unique=0, seq_in_index=1, column_name="qq", sub_part=None)),
            lambda m: m["indexes"][1].update(sub_part=8),
            lambda m: m["indexes"][1].update(column_name="qq")):
            value = deepcopy(FIXTURE["metadata"]); change(value)
            self.assertCode("SCHEMA_MISMATCH", lambda: center.validate_schema(**value))

    def test_schema_allows_extra_nonunique_index_and_mysql_integer_display_width(self):
        value = deepcopy(FIXTURE["metadata"])
        value["columns"][0]["column_type"] = "bigint(20) unsigned"
        value["indexes"].append(dict(table_name=center.TABLES[0], index_name="extra_lookup", non_unique=1,
                                      seq_in_index=1, column_name="updated_at", sub_part=None))
        center.validate_schema(**value)

    def test_schema_rejects_non_strict_session_and_non_utc(self):
        for session in (dict(sql_mode="NO_ENGINE_SUBSTITUTION", time_zone="+00:00"),
                        dict(sql_mode="STRICT_ALL_TABLES", time_zone="SYSTEM")):
            value = deepcopy(FIXTURE["metadata"]); value["session"] = session
            self.assertCode("SCHEMA_MISMATCH", lambda: center.validate_schema(**value))

    def test_schema_rejects_connection_charset_that_cannot_preserve_identity(self):
        value = deepcopy(FIXTURE["metadata"])
        value["session"]["character_set_connection"] = "utf8mb3"
        self.assertCode("SCHEMA_MISMATCH", lambda: center.validate_schema(**value))

    def test_preflight_uses_only_metadata_zero_rows_and_explain_without_analyze(self):
        for tuples in (True, False):
            cursor = Cursor(tuples=tuples)
            center.inspect_center(cursor)
            statements = [sql for sql, values in cursor.statements]
            self.assertEqual(sum(sql.startswith("EXPLAIN ") for sql in statements), 4)
            self.assertTrue(all(sql.startswith(("SELECT ", "EXPLAIN ")) for sql in statements))
            self.assertFalse(any("ANALYZE" in sql or "FROM bindings " in sql for sql in statements))
            self.assertTrue(all("WHERE 1=0" in sql for sql in statements if sql.startswith("SELECT ") and "FROM bindings_" in sql))

    def test_readonly_preflight_does_not_request_write_permission(self):
        cursor = Cursor()
        center.inspect_center(cursor, write_access=False)
        self.assertFalse(any(sql.startswith("EXPLAIN") for sql, _ in cursor.statements))

    def test_checked_warehouse_rejects_before_business_query_and_rolls_back(self):
        value = deepcopy(FIXTURE["metadata"]); value["tables"].pop()
        connection = Connection(Cursor(metadata=value))
        store = center.CheckedOfficialBindings(lambda: connection)
        self.assertCode("SCHEMA_MISMATCH", lambda: store.get_operation(prior.ENV, prior.OP_A, group_id=prior.GROUP))
        self.assertEqual((connection.commits, connection.rollbacks), (0, 1))
        self.assertTrue(connection.closed and connection.cur.closed)
        self.assertFalse(any("WHERE operation_id=" in sql for sql, _ in connection.cur.statements))

    def test_privilege_failure_is_safe_and_never_retried(self):
        connection = Connection(Cursor(failure=("EXPLAIN INSERT", 1142)))
        calls = []
        store = center.CheckedOfficialBindings(lambda: calls.append(True) or connection)
        self.assertCode("UNAUTHORIZED", lambda: store.get_operation(prior.ENV, prior.OP_A, group_id=prior.GROUP))
        self.assertEqual(len(calls), 1)
        self.assertEqual(connection.commits, 0)

    def test_unsupported_explain_does_not_silently_skip_permission_gate(self):
        connection = Connection(Cursor(failure=("EXPLAIN INSERT", 1235)))
        store = center.CheckedOfficialBindings(lambda: connection)
        self.assertCode("NOT_SUPPORTED", lambda: store.get_operation(prior.ENV, prior.OP_A, group_id=prior.GROUP))
        self.assertEqual((connection.commits, connection.rollbacks), (0, 1))

    def test_checked_warehouse_success_preserves_operation_and_unknown_commit(self):
        row = FIXTURE["operations"][3]["row"]
        connection = Connection(Cursor(operation=row))
        store = center.CheckedOfficialBindings(lambda: connection)
        result = store.get_operation(prior.ENV, prior.OP_A, group_id=prior.GROUP)
        self.assertEqual((result["state"], result["payload_hash"]), ("COMMITTED", row["payload_hash"]))
        connection = Connection(Cursor(operation=row), commit_error=True)
        store = center.CheckedOfficialBindings(lambda: connection)
        self.assertCode("RESULT_UNKNOWN", lambda: store.get_operation(prior.ENV, prior.OP_A, group_id=prior.GROUP))
        self.assertEqual(connection.commits, 1)

    def test_factory_does_not_read_settings_while_inactive(self):
        def forbidden(): raise AssertionError("must not read any settings")
        factory = center.OfficialMySQLFactory(forbidden, lambda: False, connect_once=forbidden)
        self.assertCode("NOT_SUPPORTED", factory)

    def test_factory_single_connect_tls_deadlines_and_no_option_file(self):
        calls, connection = [], Connection()
        config = settings()
        factory = center.OfficialMySQLFactory(lambda: config, lambda: True,
            connect_once=lambda **options: calls.append(options) or connection)
        self.assertIs(factory(), connection)
        self.assertEqual(len(calls), 1)
        options = calls[0]
        self.assertEqual([options[k] for k in ("connect_timeout", "read_timeout", "write_timeout")], [5, 5, 5])
        self.assertTrue(options["ssl_verify_cert"] and options["ssl_verify_identity"])
        self.assertFalse(options["autocommit"] or options["local_infile"])
        self.assertEqual(connection.cur.statements, [("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ", ())])
        self.assertNotIn("read_default_file", options)
        self.assertEqual(config, settings())

    def test_factory_rejects_url_failover_or_option_file_before_connect(self):
        for change in (dict(read_default_file="SYNTHETIC_FORBIDDEN.ini"),
                       dict(host="db.example.invalid,other.example.invalid"), dict(port=True), dict(database="bad?options")):
            config = settings(); config.update(change); calls = []
            factory = center.OfficialMySQLFactory(lambda: config, lambda: True,
                connect_once=lambda **options: calls.append(options))
            self.assertCode("DB_UNAVAILABLE", factory)
            self.assertEqual(calls, [])

    def test_factory_closes_if_instance_stops_during_open_and_sanitizes_auth_failure(self):
        active, connection = iter((True, True, False)), Connection()
        factory = center.OfficialMySQLFactory(settings, lambda: next(active), connect_once=lambda **_: connection)
        self.assertCode("NOT_SUPPORTED", factory)
        self.assertTrue(connection.closed)
        calls = []
        def fail(**options):
            calls.append(True)
            raise RuntimeError(1045, "SYNTHETIC_PRIVATE_PASSWORD")
        self.assertCode("UNAUTHORIZED", center.OfficialMySQLFactory(settings, lambda: True, connect_once=fail))
        self.assertEqual(len(calls), 1)

    def test_manual_ddl_matches_contract_and_never_touches_old_table(self):
        sql = (ROOT / "sql" / "qq_official_v1.sql").read_text(encoding="utf-8")
        fence = chr(96) * 3
        contract = (ROOT / "docs" / "QQ_OFFICIAL_IMPLEMENTATION_CONTRACT.md").read_text(encoding="utf-8")
        section = contract.split("## 附录 C：", 1)[1].split(fence + "sql\n", 1)[1].split(fence, 1)[0]
        self.assertTrue(sql.endswith(section))
        self.assertEqual(re.findall(r"^CREATE TABLE (\w+)", sql, re.M), list(center.TABLES))
        self.assertFalse(re.search(r"^(?:USE|ALTER|DROP|DELETE|INSERT|UPDATE)\b", sql, re.M | re.I))


if __name__ == "__main__":
    unittest.main()
