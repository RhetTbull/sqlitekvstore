# Sqlite Key Value Store

This is a simple key value store backed by sqlite. It is intended for cases where you need to persistently store small amounts of data.

## Rationale

I write a lot python command line utilities, some of which need to persistently store data, for example, to resume a previous operation. After experimenting with different solutions, such as plain JSON, I've found that sqlite offers an optimal mix of performance, portability, and ease of use. However, I got tired of writing the same boilerplate code over and over to create and use a simple sqlite database hence this package. There are a number of other sqlite key value stores out there, but none of them were exactly what I was looking for so I rolled my own.

## Installation

Copy `sqlitekvstore.py` to your python path and import it.

## Usage

### Basic Usage

```pycon
>>> from sqlitekvstore import SqliteKeyValueStore
>>> kv = SqliteKeyValueStore("data.db")
>>> kv.set("foo", "bar")
>>> kv.get("foo")
'bar'
>>> kv["foo"]
'bar'
>>> kv.delete("foo")
>>> kv.get("foo")
>>> 
>>> kv.close()
>>> 
```

### Context Manager

Note: You can set `wal=True` to enable [Sqlite WAL mode](https://www.sqlite.org/wal.html) which will provide much better performance, particularly when writing a lot of key/value pairs.

```pycon
>>> from sqlitekvstore import SqliteKeyValueStore
>>> with SqliteKeyValueStore("data.db", wal=True) as kv:
...     kv.set("foo", "bar")
...     assert kv.get("foo") == "bar"
...
>>>
```

### Dictionary Interface

SqliteKeyValueStore supports the standard dictionary interface.

```pycon
>>> from sqlitekvstore import SqliteKeyValueStore
>>> kv = SqliteKeyValueStore("data.db")
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

### Custom Value Types
 
Keys must be a type supported by sqlite (strings, bytes, integers, or floats). Values can be any type as long as you provide a serializer/deserializer function. Here's an example using JSON:

```pycon
>>> from sqlitekvstore import SqliteKeyValueStore
>>> import json
>>> kv = SqliteKeyValueStore("data.db", serialize=json.dumps, deserialize=json.loads)
>>> kv["foo"] = {"bax": "buz"}
>>> kv["foo"]
{'bax': 'buz'}
>>>
```

And here's an example using pickle:

```pycon
>>> from sqlitekvstore import SqliteKeyValueStore
>>> import pickle
>>> import datetime
>>> kv = SqliteKeyValueStore("pickle.db", serialize=pickle.dumps, deserialize=pickle.loads)
>>> kv["date1"] = datetime.datetime(2022,8,30,0,0,0)
>>> kv["date1"]
datetime.datetime(2022, 8, 30, 0, 0)
>>>
```

## Limitations and Implementation Notes

* Keys must be a type directly supported by sqlite, e.g. strings, bytes, integers, or floats.
* Keys must be unique.
* Values must be a type directly supported by sqlite, e.g. strings, bytes, integers, or floats however you may be provide a custom serializer/deserializer to serialize/deserialize your values to `SqliteKeyValueStore.__init__()` and this will be used for all operations.
* Keys and values are stored using using sqlite's `BLOB` type.
* To keep the database a single file, WAL mode is not enabled by default. If you need to store many keys, you should enable WAL mode which will significantly improve performance but will also create additional journal files for your database.

## Testing

100% test coverage. 100% mypy type checking.

## License

Copyright (c) 2022, Rhet Turnbull, All rights reserved.

Licensed under the MIT License.

## Contributing

Contributions are welcome. Please open an issue or pull request if you find a bug or want to contribute.
