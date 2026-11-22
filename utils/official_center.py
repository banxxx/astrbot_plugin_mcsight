"""Explicit MySQL/TiDB connection and schema boundary; no config/data at import.

Only the owner may supply settings. Nothing searches environment variables,
option files, the old OneBot pool, user profiles, or a default database.
"""
import re

from .official_bindings import BindingError, OfficialBindings


TABLES = ("bindings_qqofficial", "bindings_qqofficial_ops")
SESSION_SQL = ("SELECT @@SESSION.sql_mode AS sql_mode, @@SESSION.time_zone AS time_zone,"
               " @@SESSION.character_set_connection AS character_set_connection")
TABLE_SQL = ("SELECT TABLE_NAME AS table_name, TABLE_TYPE AS table_type, ENGINE AS engine,"
             " TABLE_COLLATION AS table_collation FROM information_schema.TABLES"
             " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('bindings_qqofficial','bindings_qqofficial_ops')")
COLUMN_SQL = ("SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, DATA_TYPE AS data_type,"
              " COLUMN_TYPE AS column_type, IS_NULLABLE AS is_nullable, CHARACTER_MAXIMUM_LENGTH AS char_length,"
              " COLLATION_NAME AS collation_name, DATETIME_PRECISION AS datetime_precision, EXTRA AS extra"
              " FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()"
              " AND TABLE_NAME IN ('bindings_qqofficial','bindings_qqofficial_ops')")
INDEX_SQL = ("SELECT TABLE_NAME AS table_name, INDEX_NAME AS index_name, NON_UNIQUE AS non_unique,"
             " SEQ_IN_INDEX AS seq_in_index, COLUMN_NAME AS column_name, SUB_PART AS sub_part"
             " FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE()"
             " AND TABLE_NAME IN ('bindings_qqofficial','bindings_qqofficial_ops')")


def schema_spec():
    """Version-one shape, also compared against the Java synthetic fixture."""
    def col(kind, *, nullable=False, length=None, collation=None, auto=False):
        return dict(data_type=kind, unsigned=kind == "bigint", nullable=nullable,
                    char_length=length, collation_name=collation,
                    datetime_precision=6 if kind == "datetime" else None,
                    auto_increment=auto)
    def ascii_col(kind="char", length=36, nullable=False):
        return col(kind, nullable=nullable, length=length, collation="ascii_bin")
    def text_col(nullable=False):
        return col("varchar", nullable=nullable, length=64, collation="utf8mb4_bin")
    binding = dict(id=col("bigint", auto=True), environment_id=ascii_col(), group_id=text_col(),
        game_key=ascii_col("varchar", 16), game_id=text_col(), qq=text_col(),
        state=ascii_col("varchar", 16), revision=col("bigint"), last_operation_id=ascii_col(),
        bind_time=col("datetime"), updated_at=col("datetime"))
    operation = dict(operation_id=ascii_col(), environment_id=ascii_col(), group_id=text_col(),
        request_key=ascii_col(length=64), payload_hash=ascii_col(length=64),
        kind=ascii_col("varchar", 16), actor_kind=ascii_col("varchar", 16), actor_id=text_col(),
        target_user_id=text_col(), original_server_id=ascii_col(nullable=True),
        token_generation=ascii_col(nullable=True), reservation_id=ascii_col(nullable=True),
        policy_revision=col("bigint"), intent=col("json"), nickname_basis=col("json", nullable=True),
        state=ascii_col("varchar", 16), result_code=ascii_col("varchar", 32, True),
        effects=col("json"), followup=col("json"), next_attempt_at=col("datetime", nullable=True),
        created_at=col("datetime"), updated_at=col("datetime"))
    return {
        TABLES[0]: dict(columns=binding, indexes={
            "PRIMARY": [True, ["id"]],
            "uq_official_game": [True, ["environment_id", "group_id", "game_key"]],
            "ix_official_user": [False, ["environment_id", "group_id", "qq", "state"]]}),
        TABLES[1]: dict(columns=operation, indexes={
            "PRIMARY": [True, ["operation_id"]],
            "uq_official_request": [True, ["environment_id", "request_key"]],
            "uq_official_token": [True, ["environment_id", "original_server_id", "token_generation"]],
            "ix_official_pending": [False, ["environment_id", "state", "next_attempt_at"]],
            "ix_official_actor": [False, ["environment_id", "group_id", "actor_id", "created_at"]]})}


def validate_schema(tables, columns, indexes, session):
    """Validate metadata only; no binding or operation rows are read here."""
    try:
        modes = set(session["sql_mode"].upper().split(","))
        if (not modes.intersection({"STRICT_ALL_TABLES", "STRICT_TRANS_TABLES"})
                or session["time_zone"] != "+00:00" or session["character_set_connection"] != "utf8mb4"):
            raise ValueError
        spec = schema_spec()
        if len(tables) != 2 or {row["table_name"] for row in tables} != set(TABLES):
            raise ValueError
        for row in tables:
            if (row["table_type"] != "BASE TABLE" or row["engine"].upper() != "INNODB"
                    or row["table_collation"] != "utf8mb4_bin"):
                raise ValueError
        seen = {name: {} for name in TABLES}
        for row in columns:
            table, name = row["table_name"], row["column_name"]
            if table not in seen or name in seen[table]:
                raise ValueError
            extra = row["extra"].lower().strip()
            if extra not in ("", "auto_increment"):
                raise ValueError
            if row["is_nullable"] not in ("YES", "NO"):
                raise ValueError
            seen[table][name] = dict(data_type=row["data_type"].lower(),
                unsigned=bool(re.search(r"\bunsigned\b", row["column_type"].lower())),
                nullable=row["is_nullable"] == "YES", char_length=row["char_length"],
                collation_name=row["collation_name"], datetime_precision=row["datetime_precision"],
                auto_increment=extra == "auto_increment")
        for table in TABLES:
            if seen[table] != spec[table]["columns"]:
                raise ValueError
        grouped = {name: {} for name in TABLES}
        for row in indexes:
            table, name = row["table_name"], row["index_name"]
            if table not in grouped or row["sub_part"] is not None or row["non_unique"] not in (0, 1):
                raise ValueError
            unique, parts = grouped[table].setdefault(name, [row["non_unique"] == 0, {}])
            seq = row["seq_in_index"]
            if (unique != (row["non_unique"] == 0) or seq in parts or seq < 1
                    or row["column_name"] not in seen[table]):
                raise ValueError
            parts[seq] = row["column_name"]
        for table, definitions in grouped.items():
            actual = {}
            for name, (unique, parts) in definitions.items():
                if sorted(parts) != list(range(1, len(parts) + 1)):
                    raise ValueError
                actual[name] = [unique, [parts[i] for i in sorted(parts)]]
            required = spec[table]["indexes"]
            if any(actual.get(name) != value for name, value in required.items()):
                raise ValueError
            # An extra UNIQUE(qq), or any other extra unique key, breaks the contract.
            if any(value[0] for name, value in actual.items() if name not in required):
                raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise BindingError("SCHEMA_MISMATCH", "官方中心结构或会话设置不符合版本一契约。") from None


def _rows(cursor, sql, limit):
    cursor.execute(sql)
    values = cursor.fetchmany(limit + 1)
    if len(values) > limit:
        raise BindingError("SCHEMA_MISMATCH", "官方中心元数据超出预期范围。")
    return [dict(row) if isinstance(row, dict) else
            {field[0]: row[i] for i, field in enumerate(cursor.description)} for row in values]


def inspect_center(cursor, *, write_access=True):
    session = _rows(cursor, SESSION_SQL, 1)
    if len(session) != 1:
        raise BindingError("SCHEMA_MISMATCH", "官方中心会话核验失败。")
    validate_schema(_rows(cursor, TABLE_SQL, 2), _rows(cursor, COLUMN_SQL, 40),
                    _rows(cursor, INDEX_SQL, 200), session[0])
    for table, definition in schema_spec().items():
        columns = ",".join(definition["columns"])
        cursor.execute(f"SELECT {columns} FROM {table} WHERE 1=0")
        if write_access:
            # EXPLAIN only, never ANALYZE or executable probe DML. An engine
            # without this privilege-planning facility must fail closed.
            def sample(name, spec):
                if spec["nullable"]:
                    return "NULL"
                if spec["data_type"] == "bigint":
                    return "1"
                if spec["data_type"] == "datetime":
                    return "'2026-11-18 00:00:00.000000'"
                if spec["data_type"] == "json":
                    return "'[]'" if name == "effects" else "'{}'"
                return "'SYNTHETIC'"
            cursor.execute(f"EXPLAIN INSERT INTO {table} ({columns}) VALUES ("
                           + ",".join(sample(name, spec) for name, spec in definition["columns"].items()) + ")")
            cursor.fetchall()
            cursor.execute(f"EXPLAIN UPDATE {table} SET "
                           + ",".join(name + "=" + name for name in definition["columns"])
                           + " WHERE 1=0")
            cursor.fetchall()


class OfficialMySQLFactory:
    """Fresh, single-host PyMySQL connection; no pool/reconnect/option file.

    settings_provider is invoked only by an active runtime. Tests must inject
    connect_once; a missing dependency or any connector error is sanitized.
    """
    def __init__(self, settings_provider, active_guard, *, connect_once=None):
        if not callable(settings_provider) or not callable(active_guard):
            raise ValueError("Explicit settings provider and active guard required")
        self._settings = settings_provider
        self._active = active_guard
        self._connect = connect_once

    def __call__(self):
        connection = None
        try:
            if not self._active():
                raise BindingError("NOT_SUPPORTED", "当前实例未开放官方中心。")
            values = self._settings()
            if not isinstance(values, dict) or set(values) != {"host", "port", "user", "password", "database", "ssl_ca"}:
                raise ValueError
            values = dict(values)
            if (not isinstance(values["host"], str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", values["host"])
                    or type(values["port"]) is not int or not 1 <= values["port"] <= 65535
                    or not isinstance(values["database"], str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", values["database"])):
                raise ValueError
            if any(not isinstance(values[key], str) or not values[key] or "\0" in values[key]
                   for key in ("user", "password", "ssl_ca")):
                raise ValueError
            connect = self._connect
            if connect is None:
                import pymysql
                connect = pymysql.connect
            if not self._active():
                raise BindingError("NOT_SUPPORTED", "当前实例未开放官方中心。")
            connection = connect(**values, charset="utf8mb4", autocommit=False,
                connect_timeout=5, read_timeout=5, write_timeout=5, local_infile=False,
                ssl_verify_cert=True, ssl_verify_identity=True,
                sql_mode="STRICT_ALL_TABLES,NO_ENGINE_SUBSTITUTION", init_command="SET time_zone = '+00:00'")
            if not self._active():
                raise BindingError("NOT_SUPPORTED", "当前实例未开放官方中心。")
            cursor = None
            try:
                cursor = connection.cursor()
                cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            finally:
                OfficialBindings._quiet(cursor, "close")
            if not self._active():
                raise BindingError("NOT_SUPPORTED", "当前实例未开放官方中心。")
            return connection
        except BindingError:
            OfficialBindings._quiet(connection, "close")
            raise
        except Exception as error:
            OfficialBindings._quiet(connection, "close")
            if error.args and error.args[0] in (1044, 1045, 1142, 1143):
                raise BindingError("UNAUTHORIZED", "官方中心连接权限不足。") from None
            raise BindingError("DB_UNAVAILABLE", "官方中心连接未建立，暂停变更。") from None


class CheckedOfficialBindings(OfficialBindings):
    """Run schema/zero-row privilege preflight before every production unit.

    Kept separate from the SQLite-only synthetic warehouse and old OneBot pool.
    It never repairs a table, creates a schema, or transparently retries.
    """
    def __init__(self, connection_factory):
        super().__init__(connection_factory)

    def _preflight(self, cursor):
        try:
            inspect_center(cursor)
        except BindingError:
            raise
        except Exception as error:
            if error.args and error.args[0] in (1064, 1235):
                raise BindingError("NOT_SUPPORTED", "中心不支持当前无写入核验方式，尚不能开放官方变更。") from None
            raise
