# Sqlite Key Value Store

This is a simple key value store backed by sqlite. It is intended for cases where you need to persistently store small amounts of data. All runtime code is dependency free and contained in a single file. Mutations are atomic, failed writes are rolled back, and one instance can be shared between threads. CI enforces 100% statement and branch coverage and strict mypy checks of the implementation.

## Rationale

I write a lot python command line utilities, some of which need to persistently store data, for example, to resume a previous operation. After experimenting with different solutions, such as plain JSON, I've found that sqlite offers an optimal mix of performance, portability, and ease of use. However, I got tired of writing the same boilerplate code over and over to create and use a simple sqlite database hence this package. There are a [number of other python sqlite key value stores](https://github.com/search?l=Python&q=sqlite+key+value&type=Repositories) out there, but none of them were exactly what I was looking for so I rolled my own.

## Other Solutions

If you need a simple key value store, consider the [dbm](https://docs.python.org/3/library/dbm.html) or [shelve](https://docs.python.org/3/library/shelve.html) packages which are part of the Python standard library.  If you need a more full featured solution, consider [diskcache](https://github.com/grantjenks/python-diskcache) which is a high-performance disk-backed cache.

## Installation

Requires Python 3.12 or newer.

### PyPI

```bash
pip install sqlitekvstore
```

### Manual

All the code is contained in a single file, `sqlitekvstore.py` which you can copy to your project.  Alternatively, you can copy it to your python path and import it. Copy `sqlitekvstore.pyi` alongside it if you also want type-checker support for a manual installation.

## Usage

### Basic Usage

```pycon
>>> from sqlitekvstore import SQLiteKVStore
>>> kv = SQLiteKVStore("data.db")
>>> kv.set("foo", "bar")
>>> kv.get("foo")
'bar'
>>> len(kv)
1
>>> "foo" in kv
True
>>> "baz" in kv
False
>>> kv.delete("foo")
>>> kv.get("foo")
>>> kv.close()
>>>
```

### Context Manager

You can use SQLiteKVStore as a context manager to automatically close the database when you're done with it. If you don't do this, you'll need to call `close()` yourself.

```pycon
>>> from sqlitekvstore import SQLiteKVStore
>>> with SQLiteKVStore("data.db") as kv:
...     kv.set("foo", "bar")
...     assert kv.get("foo") == "bar"
...
>>>
```

### Dictionary Interface

SQLiteKVStore supports the standard dictionary interface.

```pycon
>>> from sqlitekvstore import SQLiteKVStore
>>> kv = SQLiteKVStore("data.db")
>>> kv["foo"] = "bar"
>>> kv["foo"]
'bar'
>>> kv["qux"] = "baz"
>>> for k in kv:
...     print(k, kv[k])
...
foo bar
qux baz
>>> for k in kv.keys():
...     print(k)
...
foo
qux
>>> for v in kv.values():
...     print(v)
...
bar
baz
>>> for k, v in kv.items():
...     print(k, v)
...
foo bar
qux baz
>>> del kv["foo"]
>>> kv.pop("qux")
'baz'
>>>
```

### Default Values

You can provide a default value to `get()` to return if the key doesn't exist. Note that this does not create the key in the database.

```pycon
>>> from sqlitekvstore import SQLiteKVStore
>>> kv = SQLiteKVStore("data.db")
>>> kv.set("foo", "bar")
>>> kv.get("foo")
'bar'
>>> kv.get("FOO")
>>> kv.get("FOO", "BAR")
'BAR'
>>> kv.get("FOO")
>>>
```

### Custom Value Types

Keys must be a type supported by sqlite (strings, bytes, integers, or floats). Values can be any type as long as you provide a serializer/deserializer function that converts your values to an appropriate type. Here's an example using JSON:

```pycon
>>> from sqlitekvstore import SQLiteKVStore
>>> import json
>>> kv = SQLiteKVStore("data.db", serialize=json.dumps, deserialize=json.loads)
>>> kv["foo"] = {"bax": "buz"}
>>> kv["foo"]
{'bax': 'buz'}
>>>
```

And here's an example using pickle:

Only unpickle data from trusted sources.

```pycon
>>> from sqlitekvstore import SQLiteKVStore
>>> import pickle
>>> import datetime
>>> kv = SQLiteKVStore("pickle.db", serialize=pickle.dumps, deserialize=pickle.loads)
>>> kv["date1"] = datetime.datetime(2022,8,30,0,0,0)
>>> kv["date1"]
datetime.datetime(2022, 8, 30, 0, 0)
>>>
```

### Database `.about` Property

`SQLiteKVStore.about` is an optional property that can be used to set/get a description of the database.  This is useful for when you later discover a sqlite database laying around and want to inspect it to know what it was used for.  If not set, `.about` will return an empty string.

```pycon
>>> from sqlitekvstore import SQLiteKVStore
>>> kv = SQLiteKVStore("data.db")
>>> kv.about = "This is my key-value database"
>>> kv.about
'This is my key-value database'
>>>
```

### Database `.path` Property

`SQLiteKVStore.path` is a read-only property that returns the path to the database file.

```pycon
>>> from sqlitekvstore import SQLiteKVStore
>>> kv = SQLiteKVStore("data.db")
>>> kv.path
'data.db'
>>>
```

### Performance

#### WAL Mode

By default, [SQLite WAL mode](https://www.sqlite.org/wal.html) is not enabled. You can request it with `wal=True`. WAL creates additional journal files alongside the database and can improve concurrency and write performance. `wal=True` also selects `synchronous="NORMAL"` unless explicitly overridden. This preserves consistency but recent committed transactions may be lost after power failure or an operating-system crash. Use `synchronous="FULL"` for stronger WAL durability. The performance figures below reflect the historical NORMAL setting.

```pycon
>>> from sqlitekvstore import SQLiteKVStore
>>> kv = SQLiteKVStore("data.db", wal=True)
>>> kv.set("foo", "bar")
>>> kv.get("foo")
'bar'
>>> kv.close()
>>>
```

As a point of reference, here are the results of inserting and then reading 10,000 key/value pairs into a database with/without WAL mode enabled on my fairly old Macbook laptop:

    Without WAL Mode
    Insert 10000 keys in 18.17 seconds
    Get 10000 keys in 0.35 seconds
    Total 18.52 seconds

    WAL Mode
    Insert 10000 keys in 2.48 seconds
    Get 10000 keys in 0.14 seconds
    Total 2.62 seconds

#### `set_many()`

If you need to set many key/value pairs at once, you can use the `set_many()` method. This is much faster than calling `set()` in a loop because `set()` commits to the database after each call.  `set_many()` takes an iterable of (key, value) tuples or a dictionary of key:value pairs.

```pycon
>>> from sqlitekvstore import SQLiteKVStore
>>> kv = SQLiteKVStore("data_many.db")
>>> kv.set_many([("foo", "bar"), ("quz", "qax")])
>>> kv.get("foo")
'bar'
>>> kv.get("quz")
'qax'
>>> kv.set_many({"fizz": "buzz", "fuzz": "bizz"})
>>> kv.get("fizz")
'buzz'
>>> kv.get("fuzz")
'bizz'
>>>
```

### Other Features

#### `vacuum()` Method

If you insert/delete/update *a lot* of keys you may want to vacuum the database to reclaim unused space.  This can be done by calling `vacuum()`.  Reference [SQLite vacuum command](https://www.sqlite.org/lang_vacuum.html).

```pycon
>>> from sqlitekvstore import SQLiteKVStore
>>> kv = SQLiteKVStore("data.db")
>>> kv.vacuum()
>>>
```

#### `wipe()` Method

If you need to delete all keys from the database, you can call `wipe()`.

```pycon
>>> from sqlitekvstore import SQLiteKVStore
>>> kv = SQLiteKVStore("data.db")
>>> kv.set("foo", "bar")
>>> kv.get("foo")
'bar'
>>> kv.wipe()
>>> kv.get("foo")
>>>
```

## Reliability and concurrency

Every mutation commits before returning. If a statement, binding, callback, or
commit fails, that operation rolls back; a later write cannot commit its partial
results. `set_many()` is all-or-nothing, and the last value wins for repeated keys.
The batch is materialized and serialized before writing, so memory use scales with
its size. General mappings and iterables of pairs are accepted.

`pop()` atomically reads, decodes, and removes an entry. If decoding fails, the
entry remains. `del kv[key]` raises `KeyError` for an absent key; `delete(key)` is a
no-op for an absent key. `get(key, default)` returns the default only for a missing
row. Decoder errors, including `KeyError`, propagate even when a default is given.
SQLite and callback exceptions retain their original types. If rollback itself
fails, the connection is invalidated and subsequent store operations raise
`sqlite3.ProgrammingError`; the durable result may be uncertain in such a failure.

One instance can be shared between threads. Its database operations are serialized
with a lock. Separate instances and processes coordinate through SQLite, with one
writer at a time. Each process must open its own instance; do not inherit one
across `fork()`. Separate calls do not form a transaction: reading a value and
writing a modified value requires application-level coordination. The store's
context manager closes the store and does not group operations into a transaction.

Serializers and deserializers may run concurrently and must be thread-safe. They
must not call database methods on the same instance; this raises
`sqlite3.ProgrammingError`. Callbacks and registered SQLite adapters must also not
wait for another operation on the same database. `pop()` holds a write transaction
while decoding, so slow callbacks delay other writers. External SQLite adapters
must not reenter the store; the callback guard only wraps package callbacks.

### Lock timeout and durability

```python
from sqlitekvstore import SQLiteKVStore

with SQLiteKVStore("data.db", wal=True, timeout=2.0, synchronous="FULL") as kv:
    kv.set("key", "value")
```

The keyword-only `timeout` defaults to 5 seconds and controls SQLite lock waiting.
Zero requests immediate failure on contention. It is not an overall method
deadline: waiting for the instance lock and running callbacks can take longer.
There are no automatic application-level retries. A lock failure raises
`sqlite3.OperationalError`; after rollback the instance remains usable.

`synchronous` accepts `None`, `"NORMAL"`, `"FULL"`, or `"EXTRA"`. With `None`,
`wal=True` selects NORMAL; otherwise SQLite's connection setting is left alone.
An explicit setting is applied on each open. `wal=False` does not disable WAL
already persisted in the database. A file-backed WAL request that cannot activate
WAL fails explicitly. In-memory databases may remain in MEMORY journal mode.

### Opening, iteration, and cleanup

New paths, existing empty files, and `":memory:"` are initialized automatically.
Existing stores are checked for compatible tables; partial, unrelated, or
incompatible databases raise `sqlite3.DatabaseError` during construction. No
automatic repair or directory creation is performed. Existing valid schemas and
unrelated tables within a valid store are preserved. Configuration failure may
leave a valid empty database on disk; files are never removed automatically.
String paths and path objects are supported, and `path` returns the original
argument.

`keys()`, `values()`, `items()`, and `iter(kv)` fetch a complete raw snapshot when
first advanced. Order is unspecified and memory use scales with the result set.
Separate calls do not share a snapshot. Values are decoded as they are yielded;
a decoding error can interrupt iteration after earlier values were delivered.

`close()` is idempotent and coordinates with operations holding the database lock.
Database operations after close raise `sqlite3.ProgrammingError`. A read or
iterator that already fetched its raw results may finish decoding/yielding after
close. An unstarted iterator fails on first advance after close. A write whose
serialization overlaps close fails before accessing the database. Prefer the
context manager or explicit close over destructor cleanup.

### Raw SQLite access

`connection()` returns the underlying connection, including after close. Arrange
exclusive access yourself and finish all transactions before resuming store
operations. Do not change its schema, row factory, transaction settings, or
lifetime behind the store. Store database methods reject an active external
transaction rather than commit or roll back its work.

Raw SQL outside an explicit transaction now commits immediately. To group raw
statements, use an explicit transaction:

```python
conn = kv.connection()  # kv must still be open; caller ensures exclusive access
conn.execute("BEGIN IMMEDIATE").close()
try:
    conn.execute("INSERT OR REPLACE INTO data (key, value) VALUES (?, ?)",
                 ("raw", "value")).close()
    conn.commit()
except BaseException:
    conn.rollback()
    raise
```

### Wipe maintenance

`wipe()` commits atomic deletion of all entries, preserving `about`, then vacuums
the database. These are separate phases. A vacuum error propagates with an
exception note explaining that deletion already committed. Another connection may
write after deletion. Use `wipe(vacuum=False)` to skip space reclamation and run
`vacuum()` later if needed.

## Limitations and Implementation Notes

* Keys must be a type directly supported by sqlite, e.g. strings, bytes, integers, or floats.
* Keys must be unique under SQLite comparison rules: numeric keys such as `1`, `1.0`, and `True` refer to the same entry. Null and NaN keys are not supported.
* Values must be a type directly supported by sqlite, e.g. strings, bytes, integers, or floats however you may be provide a custom serializer/deserializer to serialize/deserialize your values to `SQLiteKVStore.__init__()` and this will be used for all operations.
* Keys and values use columns declared with SQLite BLOB affinity; native SQLite storage types are preserved.
* There is only a single data table per database. Use separate database files for independent stores.  You could also use a single table and prefix your keys with a table name, e.g. `table1:foo` and `table2:foo`.
* To keep the database a single file, WAL mode is not enabled by default. If you need to store many keys, you should enable WAL mode which will significantly improve performance but will also create additional journal files for your database.  Once you have enabled WAL mode on a database, it will stay enabled for that database even if you set `wal=False` in the constructor.

## Schema

The database schema is simple:

```sql
CREATE TABLE _about (id INTEGER PRIMARY KEY, description TEXT);
CREATE TABLE data (key BLOB PRIMARY KEY NOT NULL, value BLOB);
CREATE UNIQUE INDEX idx_key ON data (key);
```

## Testing

CI enforces 100% statement and branch coverage, strict mypy checking of the implementation and caller examples, stub/runtime consistency, and wheel/sdist installation checks. The tests include failed writes, commit contention, callbacks, concurrent operations, spawned processes, initialization, and resource cleanup. Linux tests cover Python 3.12–3.14; Windows and macOS jobs cover 3.12 and 3.14.

## License

Copyright (c) 2022, Rhet Turnbull, All rights reserved.

Licensed under the MIT License.

## Contributing

Contributions are welcome. Please open an issue or pull request if you find a bug or want to contribute.

### Installation for Contributors

* Fork and clone your fork
* [Install uv](https://docs.astral.sh/uv/getting-started/installation/)
* `uv sync --dev`
* Edit `sqlitekvstore.py` to add features/fix bugs
* Edit `test_sqlitekvstore.py` to add/update tests
* `uv run ruff format .`
* `uv run ruff check --fix .`
* `uv run pytest --cov`
* Open a pull request to contribute your changes
