# Sqlite Key Value Store

This is a simple key value store backed by sqlite. It is intended for cases where you need to persistently store small amounts of data. All code is dependency free and contained in a single file.  100% test coverage and 100% mypy type checking.

## Rationale

I write a lot python command line utilities, some of which need to persistently store data, for example, to resume a previous operation. After experimenting with different solutions, such as plain JSON, I've found that sqlite offers an optimal mix of performance, portability, and ease of use. However, I got tired of writing the same boilerplate code over and over to create and use a simple sqlite database hence this package. There are a [number of other python sqlite key value stores](https://github.com/search?l=Python&q=sqlite+key+value&type=Repositories) out there, but none of them were exactly what I was looking for so I rolled my own.

## Other Solutions

If you need a simple key value store, consider the [dbm](https://docs.python.org/3/library/dbm.html) or [shelve](https://docs.python.org/3/library/shelve.html) packages which are part of the Python standard library.  If you need a more full featured solution, consider [diskcache](https://github.com/grantjenks/python-diskcache) which is a high-performance disk-backed cache.

## Installation

### PyPI

```bash
pip install sqlitekvstore
```

### Manual

All the code is contained in a single file, `sqlitekvstore.py` which you can copy to your project.  Alternatively, you can copy it to your python path and import it.

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

By default, [SQLite WAL mode](https://www.sqlite.org/wal.html) is not enabled. Enabling this will provide much better performance, particularly when writing a lot of key/value pairs.  You can enable WAL mode by passing `wal=True` to the constructor.  This is not enabled by default because WAL mode causes SQLite to create additional journal files alongside the database file and for simple use cases, I prefer to maintain a single database file.

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

## Limitations and Implementation Notes

* Keys must be a type directly supported by sqlite, e.g. strings, bytes, integers, or floats.
* Keys must be unique.
* Values must be a type directly supported by sqlite, e.g. strings, bytes, integers, or floats however you may be provide a custom serializer/deserializer to serialize/deserialize your values to `SQLiteKVStore.__init__()` and this will be used for all operations.
* Keys and values are stored using using sqlite's `BLOB` type.
* There is only a single data table.  To use multiple tables, you would need to create a new `SQLiteKVStore` instance for each table.  You could also use a single table and prefix your keys with a table name, e.g. `table1:foo` and `table2:foo`.
* To keep the database a single file, WAL mode is not enabled by default. If you need to store many keys, you should enable WAL mode which will significantly improve performance but will also create additional journal files for your database.  Once you have enabled WAL mode on a database, it will stay enabled for that database even if you set `wal=False` in the constructor.

## Schema

The database schema is simple:

```sql
CREATE TABLE _about (id INTEGER PRIMARY KEY, description TEXT);
CREATE TABLE data (key BLOB PRIMARY KEY NOT NULL, value BLOB);
CREATE UNIQUE INDEX idx_key ON data (key);
```

## Testing

100% test coverage. 100% mypy type checking.

## License

Copyright (c) 2022, Rhet Turnbull, All rights reserved.

Licensed under the MIT License.

## Contributing

Contributions are welcome. Please open an issue or pull request if you find a bug or want to contribute.

### Installation for Contributors

* Fork and clone your fork
* `pip install poetry`
* `poetry install`
* Edit `sqlitekvstore.py` to add features/fix bugs
* Edit `test_sqlitekvstore.py` to add/update tests
* `black sqlitekvstore.py test_sqlitekvstore.py`
* `flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics`
* `flake8 . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics`
* `poetry run pytest --mypy --cov --doctest-glob="README.md"`
* Open a pull request to contribute your changes
