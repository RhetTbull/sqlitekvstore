"""Simple key-value store using sqlite3"""

import os.path
import sqlite3
from typing import Callable, Generator, Optional, Tuple, TypeVar

# keep mypy happy
T = TypeVar("T")


class SqliteKeyValueStore:
    """Simple Key-Value Store that uses sqlite3 database as backend"""

    def __init__(
        self,
        dbpath: str,
        serialize: Optional[Callable[[T], T]] = None,
        deserialize: Optional[Callable[[T], T]] = None,
        wal: bool = False,
    ):
        """Opens the database if it exists, otherwise creates it

        Args:
            dbpath: path to the database
            serialize: optional function to serialize values on set
            deserialize: optional function to deserialize values on get
            wal: enable write-ahead logging which may offer significant speed boost;
                once enabled, WAL mode will not be disabled, even if wal=False
        """

        if serialize and not callable(serialize):
            raise TypeError("serialize must be callable")
        if deserialize and not callable(deserialize):
            raise TypeError("deserialize must be callable")

        self._dbpath = dbpath
        self._serialize_func = serialize
        self._deserialize_func = deserialize
        self._conn = (
            sqlite3.Connection(dbpath)
            if os.path.exists(dbpath)
            else self._create_database(dbpath)
        )

        if wal:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.commit()

    def _create_database(self, dbpath: str):
        """Create the progress database"""
        conn = sqlite3.Connection(dbpath)
        cursor = conn.cursor()
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS about (
                id INTEGER PRIMARY KEY,
                description TEXT);
            """
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS
            data (key BLOB PRIMARY KEY NOT NULL, value BLOB);"""
        )
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_key ON data (key);")
        conn.commit()
        return conn

    def connection(self) -> sqlite3.Connection:
        """Return connection to underlying sqlite3 database"""
        return self._conn

    def set(self, key: T, value: T):
        """Set key:value pair"""
        serialized_value = self._serialize(value)
        conn = self.connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO data VALUES (?, ?);", (key, serialized_value)
        )
        conn.commit()

    def get(self, key: T, default: Optional[T] = None) -> Optional[T]:
        """Get value for key

        Args:
            key: key to get from key-value store
            default: optional default value to return if key not found
        Returns: value for key or default (Note: does not insert key:default into database if key does not exist)
        """
        try:
            return self._get(key)
        except KeyError:
            return default

    def delete(self, key: T):
        """Delete key from key-value store"""
        conn = self.connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM data WHERE key = ?;", (key,))
        conn.commit()

    def pop(self, key) -> Optional[T]:
        """Delete key and return value"""
        value = self[key]
        del self[key]
        return value

    def keys(self) -> Generator[T, None, None]:
        """Return keys as generator"""
        return iter(self)

    def values(self) -> Generator[T, None, None]:
        """Return values as generator"""
        conn = self.connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM data;")
        for value in cursor:
            yield self._deserialize(value[0])

    def items(self) -> Generator[Tuple[T, T], None, None]:
        """Return items (key, value) as generator"""
        conn = self.connection()
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM data;")
        for key, value in cursor:
            yield key, self._deserialize(value)

    def close(self):
        """Close the database"""
        self.connection().close()

    @property
    def about(self) -> str:
        """Return description for the database"""
        results = (
            self.connection()
            .cursor()
            .execute("SELECT description FROM about;")
            .fetchone()
        )
        return results[0] if results else ""

    @about.setter
    def about(self, description: str):
        """Set description of the database"""
        conn = self.connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO about VALUES (?, ?);",
            (
                1,
                description,
            ),
        )
        conn.commit()

    def _get(self, key: T) -> T:
        """Get value for key or raise KeyError if key not found"""
        conn = self.connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM data WHERE key = ?;", (key,))
        if result := cursor.fetchone():
            return self._deserialize(result[0])
        raise KeyError(key)

    def _serialize(self, value: T) -> T:
        """Serialize value using serialize function if provided"""
        return self._serialize_func(value) if self._serialize_func else value

    def _deserialize(self, value: T) -> T:
        """Deserialize value using deserialize function if provided"""
        return self._deserialize_func(value) if self._deserialize_func else value

    def __getitem__(self, key: T) -> T:
        return self._get(key)

    def __setitem__(self, key: T, value: T):
        self.set(key, value)

    def __delitem__(self, key: T):
        # try to get the key which will raise KeyError if key does not exist
        self._get(key)
        self.delete(key)

    def __iter__(self):
        conn = self.connection()
        cursor = conn.cursor()
        cursor.execute("SELECT key FROM data;")
        for key in cursor:
            yield key[0]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.close()

    def __len__(self):
        conn = self.connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM data;")
        return cursor.fetchone()[0]
