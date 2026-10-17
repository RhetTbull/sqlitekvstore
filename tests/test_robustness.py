"""Integrity, lifecycle, schema, and configuration regression tests."""

import contextlib
import json
import sqlite3
import subprocess
import sys
from collections import UserDict
from pathlib import Path

import pytest

from sqlitekvstore import SQLiteKVStore


@pytest.mark.parametrize("wal", [False, True])
@pytest.mark.parametrize("bad", [None, float("nan"), object(), 2**64])
def test_failed_batch_rolls_back_replacements(tmp_path, wal, bad):
    path = tmp_path / "store.db"
    with SQLiteKVStore(path, wal=wal) as store:
        store.set("existing", "original")
        with pytest.raises((sqlite3.Error, OverflowError)):
            store.set_many([("existing", "replacement"), ("partial", 1), (bad, "bad")])
        assert not store.connection().in_transaction
        assert dict(store.items()) == {"existing": "original"}
        store.set("later", "committed")
    with SQLiteKVStore(path) as reopened:
        assert dict(reopened.items()) == {"existing": "original", "later": "committed"}


@pytest.mark.parametrize(
    "operation",
    [
        lambda s: s.set("failed", "new"),
        lambda s: s.set_many([("seed", "changed"), ("failed", "new")]),
        lambda s: s.delete("seed"),
        lambda s: s.__delitem__("seed"),
        lambda s: s.pop("seed"),
        lambda s: setattr(s, "about", "changed"),
        lambda s: s.wipe(),
    ],
)
def test_real_commit_failure_recovers(tmp_path, operation):
    path = tmp_path / "store.db"
    with SQLiteKVStore(path, timeout=0) as store:
        store["seed"] = "original"
        store.about = "original"
        with contextlib.closing(sqlite3.connect(path)) as reader:
            reader.execute("BEGIN").close()
            reader.execute("SELECT * FROM data").fetchall()
            try:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    operation(store)
                assert not store.connection().in_transaction
            finally:
                reader.rollback()
        assert dict(store.items()) == {"seed": "original"}
        assert store.about == "original"
        store["later"] = "committed"
    with SQLiteKVStore(path) as reopened:
        assert dict(reopened.items()) == {"seed": "original", "later": "committed"}


@pytest.mark.parametrize("wal", [False, True])
def test_begin_contention_and_recovery(tmp_path, wal):
    path = tmp_path / "store.db"
    with SQLiteKVStore(path, wal=wal) as first, SQLiteKVStore(path, timeout=0) as second:
        conn = first.connection()
        conn.execute("BEGIN IMMEDIATE").close()
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                second.set("blocked", 1)
            assert not second.connection().in_transaction
        finally:
            conn.rollback()
        second["later"] = 2
        assert dict(first.items()) == {"later": 2}


def test_materialization_failures_do_not_write():
    with SQLiteKVStore(":memory:") as store:

        def failing_input():
            yield ("partial", 1)
            raise ValueError("input failed")

        with pytest.raises(ValueError, match="input failed"):
            store.set_many(failing_input())
        with pytest.raises(ValueError):
            store.set_many([("partial", 1), ("malformed",)])
        assert len(store) == 0
        store.set_many(UserDict({"x": 1}))
        store.set_many([("x", 2), ("x", 3)])
        store.set_many([])
        assert dict(store.items()) == {"x": 3}


@pytest.mark.parametrize("callback_name", ["serialize", "deserialize"])
@pytest.mark.parametrize("value", [False, 0, "", [], {}, 1])
def test_invalid_callbacks(callback_name, value):
    with pytest.raises(TypeError, match=callback_name):
        SQLiteKVStore(":memory:", **{callback_name: value})


def test_false_valued_callbacks_are_called():
    class FalseCallable:
        def __bool__(self):
            return False

        def __call__(self, value):
            return value + "!"

    with SQLiteKVStore(":memory:", FalseCallable(), FalseCallable()) as store:
        store["key"] = "value"
        assert store["key"] == "value!!"


@pytest.mark.parametrize("error_type", [KeyError, ValueError, KeyboardInterrupt])
def test_callback_errors_and_guard_reset(error_type):
    failing = False

    def callback(value):
        if failing:
            raise error_type("callback failed")
        return value

    with SQLiteKVStore(":memory:", callback, callback) as store:
        store["key"] = "original"
        failing = True
        for operation in [
            lambda: store.set("key", "changed"),
            lambda: store.set_many([("key", "changed")]),
            lambda: store.get("key", "default"),
            lambda: store["key"],
            lambda: list(store.values()),
            lambda: list(store.items()),
            lambda: store.pop("key"),
        ]:
            with pytest.raises(error_type):
                operation()
            assert not store.connection().in_transaction
        assert store.get("absent", "default") == "default"
        failing = False
        assert store["key"] == "original"
        del store["key"]
        assert len(store) == 0


@pytest.mark.parametrize("key", ["", b"", "❤️", "'; DROP TABLE data; --", -(2**63), 2**63 - 1, 1.25])
@pytest.mark.parametrize("value", [None, "", b"", b"\x00\xff", 0, 1.25])
def test_sqlite_values(key, value):
    with SQLiteKVStore(":memory:") as store:
        store[key] = value
        assert key in store
        assert store.get(key, object()) == value
        assert store.pop(key) == value
        assert key not in store


def test_numeric_key_equality_and_invalid_value():
    with SQLiteKVStore(":memory:") as store:
        store[1] = "integer"
        store[1.0] = "float"
        assert len(store) == 1
        assert store[True] == "float"
        with pytest.raises(sqlite3.ProgrammingError):
            store["bad"] = object()
        assert not store.connection().in_transaction


@pytest.mark.parametrize(
    "operation",
    [
        lambda s: s.get("k"),
        lambda s: s.set("k", "v"),
        lambda s: s.set_many([]),
        lambda s: s.delete("k"),
        lambda s: s.pop("k"),
        lambda s: s.__delitem__("k"),
        lambda s: s["k"],
        lambda s: "k" in s,
        lambda s: len(s),
        lambda s: s.about,
        lambda s: setattr(s, "about", "description"),
        lambda s: s.wipe(),
        lambda s: s.vacuum(),
        lambda s: list(s),
        lambda s: list(s.keys()),
        lambda s: list(s.values()),
        lambda s: list(s.items()),
        lambda s: s.__enter__(),
    ],
)
def test_closed_store_operations(operation):
    store = SQLiteKVStore(":memory:")
    store.close()
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        operation(store)
    assert store.path == ":memory:"
    with pytest.raises(sqlite3.ProgrammingError):
        store.connection().execute("SELECT 1")


@pytest.mark.parametrize("iterator_name", ["keys", "values", "items"])
def test_snapshot_and_close(iterator_name):
    store = SQLiteKVStore(":memory:")
    store.set_many([("a", 1), ("b", 2)])
    snapshot = getattr(store, iterator_name)()
    first = next(snapshot)
    store.wipe(vacuum=False)
    store["c"] = 3
    unstarted = getattr(store, iterator_name)()
    store.close()
    result = [first, *snapshot]
    assert len(result) == 2
    with pytest.raises(sqlite3.ProgrammingError):
        next(unstarted)


@pytest.mark.parametrize(
    "operation",
    [
        lambda s: s.get("k"),
        lambda s: s.set("k", "v"),
        lambda s: s.set_many([]),
        lambda s: s.vacuum(),
    ],
)
def test_external_transaction_not_adopted(operation):
    with SQLiteKVStore(":memory:") as store:
        conn = store.connection()
        conn.execute("BEGIN").close()
        conn.execute("INSERT INTO data VALUES ('external', 'pending')").close()
        try:
            with pytest.raises(sqlite3.ProgrammingError, match="external transaction"):
                operation(store)
            assert conn.in_transaction
        finally:
            conn.rollback()
        assert "external" not in store


def test_raw_writes_are_autocommitted():
    with SQLiteKVStore(":memory:") as store:
        conn = store.connection()
        assert conn.autocommit == sqlite3.LEGACY_TRANSACTION_CONTROL
        assert conn.isolation_level is None
        conn.execute("INSERT INTO data VALUES ('raw', 1)").close()
        assert not conn.in_transaction
        assert store["raw"] == 1


@pytest.mark.parametrize(
    "schema",
    [
        "CREATE TABLE unrelated (value TEXT);",
        "CREATE TABLE sqliteX (value TEXT);",
        "CREATE TABLE data (key BLOB PRIMARY KEY NOT NULL, value BLOB);",
        "CREATE TABLE _about (id INTEGER PRIMARY KEY, description TEXT);",
        "CREATE TABLE data (key TEXT PRIMARY KEY, value TEXT);",
        "CREATE VIEW data AS SELECT 1 AS key, 2 AS value;",
        "CREATE TABLE data (key BLOB PRIMARY KEY NOT NULL, value BLOB, extra TEXT);",
        "CREATE TABLE data (key BLOB PRIMARY KEY NOT NULL, value BLOB, extra AS (value));",
        "CREATE TABLE data (key BLOB PRIMARY KEY NOT NULL, value BLOB AS (key));",
    ],
)
def test_incompatible_database_preserved(tmp_path, schema):
    path = tmp_path / "foreign.db"
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.executescript(schema)
        before = conn.execute("SELECT * FROM sqlite_schema").fetchall()
    with pytest.raises(sqlite3.DatabaseError, match="schema"):
        SQLiteKVStore(path)
    with contextlib.closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT * FROM sqlite_schema").fetchall() == before


def test_empty_file_and_legacy_schema(tmp_path):
    empty = tmp_path / "empty.db"
    empty.touch()
    with SQLiteKVStore(empty) as store:
        store["key"] = "value"
        assert store.path is empty
        assert store.about == ""
    # Recreate the exact 0.4.0 schema, including its redundant index.
    legacy = tmp_path / "legacy.db"
    with contextlib.closing(sqlite3.connect(legacy)) as conn:
        conn.executescript("""
            CREATE TABLE _about (id INTEGER PRIMARY KEY, description TEXT);
            CREATE TABLE data (key BLOB PRIMARY KEY NOT NULL, value BLOB);
            CREATE UNIQUE INDEX idx_key ON data (key);
            INSERT INTO data VALUES ('legacy', 'value');
            INSERT INTO _about VALUES (1, 'legacy description');
            CREATE TABLE extra (value TEXT);
        """)
    with SQLiteKVStore(legacy) as store:
        assert store["legacy"] == "value"
        assert store.about == "legacy description"
        store["new"] = "compatible"
        store.connection().execute("DROP INDEX idx_key").close()
    with SQLiteKVStore(legacy) as store:
        assert store["new"] == "compatible"
    with contextlib.closing(sqlite3.connect(legacy)) as old_reader:
        assert old_reader.execute("SELECT value FROM data WHERE key = ?", ("new",)).fetchone() == (
            "compatible",
        )


def test_corrupt_file_and_missing_directory(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a sqlite database")
    with pytest.raises(sqlite3.DatabaseError):
        SQLiteKVStore(path)
    assert path.read_bytes() == b"not a sqlite database"
    with pytest.raises(sqlite3.OperationalError):
        SQLiteKVStore(tmp_path / "missing" / "store.db")


@pytest.mark.parametrize("timeout", [True, False, "5", None, object(), 1j])
def test_invalid_timeout_type(timeout):
    with pytest.raises(TypeError, match="timeout"):
        SQLiteKVStore(":memory:", timeout=timeout)


@pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf"), -float("inf")])
def test_invalid_timeout_value(timeout):
    with pytest.raises(ValueError, match="timeout"):
        SQLiteKVStore(":memory:", timeout=timeout)


@pytest.mark.parametrize("timeout", [0, 0.125, 5])
def test_busy_timeout(timeout):
    with SQLiteKVStore(":memory:", timeout=timeout) as store:
        assert store.connection().execute("PRAGMA busy_timeout").fetchone() == (
            int(timeout * 1000),
        )


@pytest.mark.parametrize("value", ["OFF", "full", 0, [], {}, True])
def test_invalid_synchronous(value):
    with pytest.raises(ValueError, match="synchronous"):
        SQLiteKVStore(":memory:", synchronous=value)


@pytest.mark.parametrize("wal", [False, True])
@pytest.mark.parametrize("synchronous,expected", [("NORMAL", 1), ("FULL", 2), ("EXTRA", 3)])
def test_synchronous(tmp_path, wal, synchronous, expected):
    with SQLiteKVStore(tmp_path / "store.db", wal=wal, synchronous=synchronous) as store:
        assert store.connection().execute("PRAGMA synchronous").fetchone() == (expected,)


def test_wal_defaults_persistence_and_memory(tmp_path):
    path = tmp_path / "store.db"
    with SQLiteKVStore(path, wal=True) as store:
        assert store.connection().execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert store.connection().execute("PRAGMA synchronous").fetchone() == (1,)
    with SQLiteKVStore(path) as store:
        assert store.connection().execute("PRAGMA journal_mode").fetchone() == ("wal",)
    with SQLiteKVStore(":memory:", wal=True) as store:
        assert store.connection().execute("PRAGMA journal_mode").fetchone() == ("memory",)


def test_wipe_preserves_metadata():
    with SQLiteKVStore(":memory:") as store:
        store.about = "metadata"
        store["key"] = "value"
        store.wipe(vacuum=False)
        store.wipe()
        assert len(store) == 0
        assert store.about == "metadata"
        assert not store.connection().in_transaction


class FaultConnection(sqlite3.Connection):
    fault = None
    closed = False
    rollback_fails = False
    close_fails = False
    aborted = False

    def commit(self):
        if self.fault == "commit":
            if self.aborted:
                super().rollback()
            raise KeyboardInterrupt("interrupted commit")
        return super().commit()

    def rollback(self):
        if self.rollback_fails:
            raise sqlite3.OperationalError("rollback unavailable")
        return super().rollback()

    def close(self):
        if self.close_fails:
            raise sqlite3.OperationalError("close unavailable")
        self.closed = True
        return super().close()

    def execute(self, sql, parameters=()):
        if self.fault == "vacuum" and sql == "VACUUM;":
            raise sqlite3.OperationalError("vacuum unavailable")
        if self.fault == "wal" and sql == "PRAGMA journal_mode=WAL;":
            return super().execute("PRAGMA journal_mode;")
        return super().execute(sql, parameters)


@pytest.fixture
def fault_factory(monkeypatch):
    original = sqlite3.connect
    connections = []

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs, factory=FaultConnection)
        connections.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    yield connections
    for conn in connections:
        conn.close_fails = False
        conn.close()


@pytest.mark.parametrize("aborted", [False, True])
def test_interrupted_commit_rolls_back(fault_factory, aborted):
    with SQLiteKVStore(":memory:") as store:
        conn = store.connection()
        conn.aborted = aborted
        conn.fault = "commit"
        with pytest.raises(KeyboardInterrupt):
            store["key"] = "value"
        conn.fault = None
        assert not conn.in_transaction
        assert len(store) == 0
        store["recovered"] = 1


@pytest.mark.parametrize("close_fails", [False, True])
def test_failed_rollback_invalidates(fault_factory, close_fails):
    store = SQLiteKVStore(":memory:")
    conn = store.connection()
    conn.fault = "commit"
    conn.rollback_fails = True
    conn.close_fails = close_fails
    with pytest.raises(KeyboardInterrupt) as error:
        store["key"] = "value"
    assert any("rollback failed" in note for note in error.value.__notes__)
    if close_fails:
        assert any("Connection cleanup failed" in note for note in error.value.__notes__)
    else:
        assert conn.closed
    with pytest.raises(sqlite3.ProgrammingError, match="invalidated"):
        store.get("key")
    conn.close_fails = False
    conn.close()


@pytest.mark.parametrize("body_fails", [False, True])
def test_context_cleanup_error_preserves_body(fault_factory, body_fails):
    store = SQLiteKVStore(":memory:")
    conn = store.connection()
    conn.close_fails = True
    try:
        if body_fails:
            with pytest.raises(ValueError, match="body") as error, store:
                raise ValueError("body")
            assert any("cleanup failed" in note for note in error.value.__notes__)
        else:
            with pytest.raises(sqlite3.OperationalError, match="close unavailable"), store:
                pass
    finally:
        conn.close_fails = False
        store.close()


def test_vacuum_failure_is_after_commit(fault_factory):
    with SQLiteKVStore(":memory:") as store:
        store["key"] = 1
        store.connection().fault = "vacuum"
        with pytest.raises(sqlite3.OperationalError, match="vacuum unavailable") as error:
            store.wipe()
        assert "deletion already committed" in error.value.__notes__[0]
        assert len(store) == 0
        assert not store.connection().in_transaction
        store["later"] = 2


def test_unavailable_threading(monkeypatch):
    monkeypatch.setattr(sqlite3, "threadsafety", 0)
    with pytest.raises(sqlite3.NotSupportedError, match="multithreaded"):
        SQLiteKVStore(":memory:")


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_initialization_cleanup(monkeypatch, fault_factory, cleanup_fails, tmp_path):
    def fail(self, conn):
        conn.close_fails = cleanup_fails
        raise ValueError("initialization failed")

    monkeypatch.setattr(SQLiteKVStore, "_initialize", fail)
    with pytest.raises(ValueError, match="initialization failed") as error:
        SQLiteKVStore(tmp_path / "store.db")
    if cleanup_fails:
        assert "Initialization cleanup failed" in error.value.__notes__[0]
    else:
        assert fault_factory[0].closed


def test_failed_wal_leaves_valid_empty_database(monkeypatch, fault_factory, tmp_path):
    original = SQLiteKVStore._initialize

    def initialize(self, conn):
        original(self, conn)
        conn.fault = "wal"

    monkeypatch.setattr(SQLiteKVStore, "_initialize", initialize)
    path = tmp_path / "store.db"
    with pytest.raises(sqlite3.OperationalError, match="activate WAL"):
        SQLiteKVStore(path, wal=True)
    assert fault_factory[0].closed
    with SQLiteKVStore(path) as store:
        assert len(store) == 0


def test_partial_instance_cleanup_and_connection():
    store = SQLiteKVStore.__new__(SQLiteKVStore)
    store.__del__()  # Early failure before any attributes exist is harmless.
    store._lock = __import__("threading").Lock()
    store._callback_state = __import__("threading").local()
    store._conn = None
    store._closed = True
    with pytest.raises(sqlite3.ProgrammingError, match="no connection"):
        store.connection()
    store.close()


def test_transaction_refuses_external_ownership():
    with SQLiteKVStore(":memory:") as store:
        conn = store.connection()
        conn.execute("BEGIN").close()
        try:
            with pytest.raises(sqlite3.ProgrammingError, match="adopt"):
                with store._transaction(conn):
                    pytest.fail("must not enter")
            assert conn.in_transaction
        finally:
            conn.rollback()


@pytest.mark.parametrize(
    "scenario", ["pop_races", "callback_reentry", "close_races", "open_race", "process_writes"]
)
@pytest.mark.parametrize("wal", [False, True])
def test_concurrency_subprocess(tmp_path, scenario, wal):
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("scenarios.py")),
            scenario,
            str(tmp_path / "store.db"),
            json.dumps(wal),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_valid_store_can_open_while_writer_reserved(tmp_path):
    path = tmp_path / "store.db"
    with SQLiteKVStore(path) as first:
        first["key"] = "value"
        first.connection().execute("BEGIN IMMEDIATE").close()
        try:
            with SQLiteKVStore(path, timeout=0) as second:
                assert second["key"] == "value"
        finally:
            first.connection().rollback()


def test_partial_schema_creation_is_rolled_back(monkeypatch, tmp_path):
    original = SQLiteKVStore._execute

    def fail(conn, sql, params=()):
        if sql.startswith("CREATE TABLE data"):
            raise sqlite3.OperationalError("schema creation failed")
        return original(conn, sql, params)

    monkeypatch.setattr(SQLiteKVStore, "_execute", staticmethod(fail))
    path = tmp_path / "store.db"
    with pytest.raises(sqlite3.OperationalError, match="schema creation failed"):
        SQLiteKVStore(path)
    with contextlib.closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT name FROM sqlite_schema").fetchall() == []


def test_late_serializer_failure_preserves_batch():
    def serialize(value):
        if value == "bad":
            raise ValueError("serialization failed")
        return value

    with SQLiteKVStore(":memory:", serialize=serialize) as store:
        store["existing"] = "original"
        with pytest.raises(ValueError, match="serialization failed"):
            store.set_many([("existing", "changed"), ("partial", "good"), ("broken", "bad")])
        assert dict(store.items()) == {"existing": "original"}
        assert not store.connection().in_transaction


def test_registered_sqlite_adapters_remain_supported(monkeypatch):
    class Adapted:
        def __init__(self, value):
            self.value = value

    monkeypatch.setitem(sqlite3.adapters, (Adapted, sqlite3.PrepareProtocol), lambda v: v.value)
    with SQLiteKVStore(":memory:") as store:
        store[Adapted("key")] = Adapted("value")
        assert store[Adapted("key")] == "value"


def test_synchronous_formats_only_validated_numeric_level():
    class UnusualString(str):
        def __format__(self, spec):
            return "OFF"

    with SQLiteKVStore(":memory:", synchronous=UnusualString("FULL")) as store:
        assert store.connection().execute("PRAGMA synchronous").fetchone() == (2,)


def test_schema_metadata_fallback_for_older_sqlite(monkeypatch):
    original = SQLiteKVStore._fetchall

    def fetchall(conn, sql):
        if sql.startswith("PRAGMA table_xinfo"):
            return []  # Unknown PRAGMAs return an empty result on older builds.
        return original(conn, sql)

    monkeypatch.setattr(SQLiteKVStore, "_fetchall", staticmethod(fetchall))
    with SQLiteKVStore(":memory:") as store:
        store["key"] = "value"
        assert store["key"] == "value"
