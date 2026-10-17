"""A dependency-free persistent key-value store for Python 3.12 and newer."""

import contextlib
import math
import os
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from numbers import Real
from types import TracebackType
from typing import Any, Literal, Self, TypeAlias, cast

__version__ = "0.4.0"
__all__ = ["SQLiteKVStore"]

SQLiteKey: TypeAlias = str | bytes | int | float
Path: TypeAlias = str | os.PathLike[str]


class SQLiteKVStore:
    """A small SQLite store with atomic mutations and per-instance thread locking.

    Callbacks must be thread-safe and must not reenter this instance or wait for
    other operations on this database. Open a separate instance in each process.
    """

    def __init__(
        self,
        dbpath: Path,
        serialize: Callable[[Any], Any] | None = None,
        deserialize: Callable[[Any], Any] | None = None,
        wal: bool = False,
        *,
        timeout: float = 5.0,
        synchronous: Literal["NORMAL", "FULL", "EXTRA"] | None = None,
    ) -> None:
        """Open a valid store or initialize an empty database.

        timeout controls SQLite lock waiting, not an overall operation deadline.
        wal=False leaves an existing journal mode unchanged. wal=True defaults
        to synchronous=NORMAL; select FULL for stronger WAL power-loss durability.
        The original path object is available through the path property.
        """
        self._lock = threading.Lock()
        self._callback_state = threading.local()
        self._conn: sqlite3.Connection | None = None
        self._closed = True
        self._dbpath = dbpath
        self._serialize_func = serialize
        self._deserialize_func = deserialize

        for name, callback in (("serialize", serialize), ("deserialize", deserialize)):
            if callback is not None and not callable(callback):
                raise TypeError(f"{name} must be callable or None")
        if isinstance(timeout, bool) or not isinstance(timeout, Real):
            raise TypeError("timeout must be a finite nonnegative real number")
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be finite and nonnegative")
        if synchronous is not None and (
            not isinstance(synchronous, str) or synchronous not in ("NORMAL", "FULL", "EXTRA")
        ):
            raise ValueError("synchronous must be None, NORMAL, FULL, or EXTRA")
        if sqlite3.threadsafety == 0:
            raise sqlite3.NotSupportedError("SQLite must support multithreaded use")

        conn = sqlite3.connect(
            dbpath,
            timeout=float(timeout),
            check_same_thread=False,
            autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL,
            isolation_level=None,
        )
        try:
            self._initialize(conn)
            if wal:
                mode = self._fetchone(conn, "PRAGMA journal_mode=WAL;")
                if mode != ("wal",) and not (
                    os.fspath(dbpath) == ":memory:" and mode == ("memory",)
                ):
                    raise sqlite3.OperationalError("Unable to activate WAL journal mode")
            effective_sync = synchronous if synchronous is not None else "NORMAL" if wal else None
            if effective_sync is not None:
                level = {"NORMAL": 1, "FULL": 2, "EXTRA": 3}[effective_sync]
                self._execute(conn, f"PRAGMA synchronous={level};")
        except BaseException as error:
            try:
                conn.close()
            except BaseException as cleanup:
                error.add_note(f"Initialization cleanup failed ({type(cleanup).__name__})")
            raise
        self._conn = conn
        self._closed = False

    @staticmethod
    def _execute(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
        with contextlib.closing(conn.execute(sql, params)) as cursor:
            return cursor.rowcount

    @staticmethod
    def _fetchone(
        conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
    ) -> tuple[Any, ...] | None:
        with contextlib.closing(conn.execute(sql, params)) as cursor:
            return cast(tuple[Any, ...] | None, cursor.fetchone())

    @staticmethod
    def _fetchall(conn: sqlite3.Connection, sql: str) -> list[tuple[Any, ...]]:
        with contextlib.closing(conn.execute(sql)) as cursor:
            return cursor.fetchall()

    @classmethod
    def _schema_empty(cls, conn: sqlite3.Connection) -> bool:
        return (
            cls._fetchone(
                conn, "SELECT 1 FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' LIMIT 1;"
            )
            is None
        )

    def _initialize(self, conn: sqlite3.Connection) -> None:
        if self._schema_empty(conn):
            with self._transaction(conn):
                # Another opener may have created the schema while BEGIN waited.
                if self._schema_empty(conn):
                    self._execute(
                        conn, "CREATE TABLE _about (id INTEGER PRIMARY KEY, description TEXT);"
                    )
                    self._execute(
                        conn, "CREATE TABLE data (key BLOB PRIMARY KEY NOT NULL, value BLOB);"
                    )
                    self._execute(conn, "CREATE UNIQUE INDEX idx_key ON data (key);")
                self._validate_schema(conn)
        else:
            self._validate_schema(conn)

    def _validate_schema(self, conn: sqlite3.Connection) -> None:
        expected = {
            "data": [("key", "BLOB", 1, 1), ("value", "BLOB", 0, 0)],
            "_about": [("id", "INTEGER", 0, 1), ("description", "TEXT", 0, 0)],
        }
        for table, columns in expected.items():
            kind = self._fetchone(conn, "SELECT type FROM sqlite_master WHERE name = ?;", (table,))
            # table_xinfo includes hidden/generated columns, which are not supported.
            rows = self._fetchall(conn, f'PRAGMA table_xinfo("{table}");')
            if not rows:
                # Older SQLite builds lack table_xinfo and generated columns.
                rows = [(*row, 0) for row in self._fetchall(conn, f'PRAGMA table_info("{table}");')]
            actual = [(r[1], r[2].upper(), r[3], r[5]) for r in rows]
            if kind != ("table",) or actual != columns or any(r[6] for r in rows):
                raise sqlite3.DatabaseError(f"Incompatible SQLiteKVStore schema: {table}")

    def _check_callback(self) -> None:
        if getattr(self._callback_state, "active", False):
            raise sqlite3.ProgrammingError("Callbacks must not reenter the same SQLiteKVStore")

    @contextlib.contextmanager
    def _locked(self) -> Iterator[sqlite3.Connection]:
        self._check_callback()
        with self._lock:
            if self._closed or self._conn is None:
                raise sqlite3.ProgrammingError("SQLiteKVStore is closed or invalidated")
            if self._conn.in_transaction:
                raise sqlite3.ProgrammingError(
                    "Finish the external transaction before store operations"
                )
            yield self._conn

    @contextlib.contextmanager
    def _transaction(self, conn: sqlite3.Connection) -> Iterator[None]:
        if conn.in_transaction:
            raise sqlite3.ProgrammingError("Cannot adopt an external transaction")
        self._execute(conn, "BEGIN IMMEDIATE;")
        try:
            yield
            conn.commit()
        except BaseException as error:
            try:
                if conn.in_transaction:
                    conn.rollback()
            except BaseException as cleanup:
                error.add_note(f"Transaction rollback failed ({type(cleanup).__name__})")
                self._closed = True
                try:
                    conn.close()
                except BaseException as close_error:
                    error.add_note(f"Connection cleanup failed ({type(close_error).__name__})")
            raise

    def _callback(self, func: Callable[[Any], Any] | None, value: Any) -> Any:
        if func is None:
            return value
        self._callback_state.active = True
        try:
            return func(value)
        finally:
            self._callback_state.active = False

    def connection(self) -> sqlite3.Connection:
        """Return the raw, caller-managed connection, even after close.

        Arrange exclusive access and finish transactions before using the store
        again. Raw writes outside an explicit BEGIN commit immediately.
        """
        self._check_callback()
        with self._lock:
            if self._conn is None:
                raise sqlite3.ProgrammingError("SQLiteKVStore has no connection")
            return self._conn

    def set(self, key: Any, value: Any) -> None:
        """Set a value, committing atomically before returning."""
        with self._locked():
            pass
        value = self._callback(self._serialize_func, value)
        with self._locked() as conn, self._transaction(conn):
            self._execute(
                conn, "INSERT OR REPLACE INTO data (key, value) VALUES (?, ?);", (key, value)
            )

    def set_many(self, items: Mapping[Any, Any] | Iterable[tuple[Any, Any]]) -> None:
        """Commit a whole batch or none of it; materializes input before writing.

        Duplicate keys retain their last value. Memory use scales with the batch.
        """
        with self._locked():
            pass
        source = items.items() if isinstance(items, Mapping) else items
        values = [(key, self._callback(self._serialize_func, value)) for key, value in source]
        with self._locked() as conn:
            if values:
                with self._transaction(conn), contextlib.closing(conn.cursor()) as cursor:
                    cursor.executemany(
                        "INSERT OR REPLACE INTO data (key, value) VALUES (?, ?);", values
                    )

    def get(self, key: Any, default: Any = None) -> Any:
        """Return default only for an absent key; decoding failures propagate."""
        with self._locked() as conn:
            row = self._fetchone(conn, "SELECT value FROM data WHERE key = ?;", (key,))
        return default if row is None else self._callback(self._deserialize_func, row[0])

    def delete(self, key: Any) -> None:
        """Delete a key; absence is a successful no-op."""
        with self._locked() as conn, self._transaction(conn):
            self._execute(conn, "DELETE FROM data WHERE key = ?;", (key,))

    def pop(self, key: Any) -> Any:
        """Atomically decode and remove a key; decoding failure preserves it."""
        with self._locked() as conn, self._transaction(conn):
            row = self._fetchone(conn, "SELECT value FROM data WHERE key = ?;", (key,))
            if row is None:
                raise KeyError(key)
            value = self._callback(self._deserialize_func, row[0])
            self._execute(conn, "DELETE FROM data WHERE key = ?;", (key,))
        return value

    def keys(self) -> Iterator[SQLiteKey]:
        """Iterate a snapshot taken on first advance, in unspecified order."""
        return iter(self)

    def values(self) -> Iterator[Any]:
        """Fetch raw values on first advance, then decode while yielding."""
        with self._locked() as conn:
            rows = self._fetchall(conn, "SELECT value FROM data;")
        for row in rows:
            yield self._callback(self._deserialize_func, row[0])

    def items(self) -> Iterator[tuple[SQLiteKey, Any]]:
        """Fetch a snapshot on first advance; memory use scales with the store."""
        with self._locked() as conn:
            rows = self._fetchall(conn, "SELECT key, value FROM data;")
        for key, value in rows:
            yield key, self._callback(self._deserialize_func, value)

    def close(self) -> None:
        """Close once, coordinating with active database operations."""
        self._check_callback()
        with self._lock:
            if not self._closed and self._conn is not None:
                self._conn.close()
                self._closed = True

    @property
    def about(self) -> str:
        """Return the database description, or an empty string if unset."""
        with self._locked() as conn:
            row = self._fetchone(conn, "SELECT description FROM _about WHERE id = 1;")
        return cast(str, row[0]) if row is not None else ""

    @about.setter
    def about(self, description: str) -> None:
        with self._locked() as conn, self._transaction(conn):
            self._execute(
                conn,
                "INSERT OR REPLACE INTO _about (id, description) VALUES (1, ?);",
                (description,),
            )

    @property
    def path(self) -> Path:
        """Return the original path argument, including after close."""
        return self._dbpath

    def wipe(self, *, vacuum: bool = True) -> None:
        """Commit deletion of all entries, then optionally vacuum.

        If vacuum fails, deletion has already committed. Metadata is preserved.
        """
        with self._locked() as conn:
            with self._transaction(conn):
                self._execute(conn, "DELETE FROM data;")
            if vacuum:
                try:
                    self._execute(conn, "VACUUM;")
                except BaseException as error:
                    error.add_note("wipe() deletion already committed; vacuum failed")
                    raise

    def vacuum(self) -> None:
        """Reclaim unused database space outside a transaction."""
        with self._locked() as conn:
            self._execute(conn, "VACUUM;")

    def __getitem__(self, key: Any) -> Any:
        with self._locked() as conn:
            row = self._fetchone(conn, "SELECT value FROM data WHERE key = ?;", (key,))
        if row is None:
            raise KeyError(key)
        return self._callback(self._deserialize_func, row[0])

    def __setitem__(self, key: Any, value: Any) -> None:
        self.set(key, value)

    def __delitem__(self, key: Any) -> None:
        with self._locked() as conn, self._transaction(conn):
            if self._execute(conn, "DELETE FROM data WHERE key = ?;", (key,)) == 0:
                raise KeyError(key)

    def __iter__(self) -> Iterator[SQLiteKey]:
        with self._locked() as conn:
            rows = self._fetchall(conn, "SELECT key FROM data;")
        for row in rows:
            yield row[0]

    def __contains__(self, key: Any) -> bool:
        with self._locked() as conn:
            return self._fetchone(conn, "SELECT 1 FROM data WHERE key = ?;", (key,)) is not None

    def __enter__(self) -> Self:
        with self._locked():
            pass
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_traceback: TracebackType | None,
    ) -> None:
        try:
            self.close()
        except BaseException as cleanup:
            if exc_value is None:
                raise
            exc_value.add_note(f"Store cleanup failed ({type(cleanup).__name__})")

    def __len__(self) -> int:
        with self._locked() as conn:
            row = self._fetchone(conn, "SELECT COUNT(*) FROM data;")
        return cast(tuple[int], row)[0]

    def __del__(self) -> None:
        # Partial construction and interpreter teardown must not raise.
        with contextlib.suppress(Exception):
            self.close()
