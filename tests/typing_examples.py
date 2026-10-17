"""Caller examples checked against the installed distribution's annotations."""

import json
import pickle
import sqlite3
from collections import UserDict
from collections.abc import Iterator
from pathlib import Path
from typing import Any, assert_type

from sqlitekvstore import SQLiteKVStore

with SQLiteKVStore(Path("typed.db"), json.dumps, json.loads, timeout=0.5) as store:
    assert_type(store, SQLiteKVStore)
    store["document"] = {"a": [1, 2, 3]}
    store[1] = ["heterogeneous", "keys"]
    store.set_many(UserDict({"x": None}))
    store.set_many((pair for pair in [("y", True)]))
    assert_type(store.get("document"), Any)
    assert_type(store.get("absent", {"default": 1}), Any)
    assert_type(store.items(), Iterator[tuple[str | bytes | int | float, Any]])
    assert_type(store.connection(), sqlite3.Connection)
    assert_type(store.about, str)
    assert_type(store.set("key", "value"), None)
    assert_type(store.__contains__("key"), bool)
    assert_type(store.__len__(), int)
    store.wipe(vacuum=False)

with SQLiteKVStore(":memory:", pickle.dumps, pickle.loads, synchronous="FULL") as store:
    store["bytes"] = b"\x00\xff"
    assert store.pop("bytes") == b"\x00\xff"
