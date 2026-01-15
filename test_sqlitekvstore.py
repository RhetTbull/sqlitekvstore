"""Test sqlitekvstore"""

import gzip
import json
import pickle
import sqlite3
import threading
from typing import Any

import pytest

import sqlitekvstore


def pickle_and_zip(data: Any) -> bytes:
    """
    Pickle and gzip data.

    Args:
        data: data to pickle and gzip (must be pickle-able)

    Returns:
        bytes of gzipped pickled data
    """
    pickled = pickle.dumps(data)
    return gzip.compress(pickled)


def unzip_and_unpickle(data: bytes) -> Any:
    """
    Unzip and unpickle data.

    Args:
        data: data to unzip and unpickle

    Returns:
        unpickled data
    """
    return pickle.loads(gzip.decompress(data))


def test_basic_get_set(tmpdir):
    """Test basic functionality"""
    dbpath = tmpdir / "kvtest.db"
    kvstore = sqlitekvstore.SQLiteKVStore(dbpath)
    kvstore.set("foo", "bar")
    assert kvstore.get("foo") == "bar"
    assert kvstore.get("FOOBAR") is None
    kvstore.delete("foo")
    assert kvstore.get("foo") is None
    kvstore.set("baz", None)
    assert kvstore.get("baz") is None

    kvstore.close()

    # verify that the connection is closed
    conn = kvstore.connection()
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("PRAGMA user_version;")


def test_basic_get_set_wal(tmpdir):
    """Test basic functionality with WAL mode"""
    dbpath = tmpdir / "kvtest.db"
    kvstore = sqlitekvstore.SQLiteKVStore(dbpath, wal=True)
    kvstore.set("foo", "bar")
    assert kvstore.get("foo") == "bar"
    assert kvstore.get("FOOBAR") is None
    kvstore.delete("foo")
    assert kvstore.get("foo") is None
    kvstore.set("baz", None)
    assert kvstore.get("baz") is None

    kvstore.vacuum()

    kvstore.close()

    # verify that the connection is closed
    conn = kvstore.connection()
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("PRAGMA user_version;")


def test_set_many(tmpdir):
    """Test set_many()"""
    dbpath = tmpdir / "kvtest.db"

    kvstore = sqlitekvstore.SQLiteKVStore(dbpath)
    kvstore.set_many([("foo", "bar"), ("baz", "qux")])
    assert kvstore.get("foo") == "bar"
    assert kvstore.get("baz") == "qux"
    kvstore.close()

    # make sure values got committed
    kvstore = sqlitekvstore.SQLiteKVStore(dbpath)
    assert kvstore.get("foo") == "bar"
    assert kvstore.get("baz") == "qux"
    kvstore.close()


def test_set_many_dict(tmpdir):
    """Test set_many() with dict of values"""
    dbpath = tmpdir / "kvtest.db"

    kvstore = sqlitekvstore.SQLiteKVStore(dbpath)
    kvstore.set_many({"foo": "bar", "baz": "qux"})
    assert kvstore.get("foo") == "bar"
    assert kvstore.get("baz") == "qux"
    kvstore.close()

    # make sure values got committed
    kvstore = sqlitekvstore.SQLiteKVStore(dbpath)
    assert kvstore.get("foo") == "bar"
    assert kvstore.get("baz") == "qux"
    kvstore.close()


def test_basic_context_handler(tmpdir):
    """Test basic functionality with context handler"""

    dbpath = tmpdir / "kvtest.db"
    with sqlitekvstore.SQLiteKVStore(dbpath) as kvstore:
        kvstore.set("foo", "bar")
        assert kvstore.get("foo") == "bar"
        assert kvstore.get("FOOBAR") is None
        kvstore.delete("foo")
        assert kvstore.get("foo") is None

    # verify that the connection is closed
    conn = kvstore.connection()
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("PRAGMA user_version;")


def test_about(tmpdir):
    """Test about property"""
    dbpath = tmpdir / "kvtest.db"
    with sqlitekvstore.SQLiteKVStore(dbpath) as kvstore:
        kvstore.about = "My description"
        assert kvstore.about == "My description"
        kvstore.about = "My new description"
        assert kvstore.about == "My new description"


def test_existing_db(tmpdir):
    """Test that opening an existing database works as expected"""
    dbpath = tmpdir / "kvtest.db"
    with sqlitekvstore.SQLiteKVStore(dbpath) as kvstore:
        kvstore.set("foo", "bar")

    with sqlitekvstore.SQLiteKVStore(dbpath) as kvstore:
        assert kvstore.get("foo") == "bar"


def test_dict_interface(tmpdir):
    """ "Test dict interface"""
    dbpath = tmpdir / "kvtest.db"
    with sqlitekvstore.SQLiteKVStore(dbpath) as kvstore:
        kvstore["foo"] = "bar"
        assert kvstore["foo"] == "bar"
        assert len(kvstore) == 1
        assert kvstore.get("foo") == "bar"

        assert "foo" in kvstore
        assert "FOOBAR" not in kvstore

        assert kvstore.pop("foo") == "bar"
        assert kvstore.get("foo") is None

        kvstore["❤️"] = "💖"
        assert kvstore["❤️"] == "💖"
        assert kvstore.get("❤️") == "💖"

        del kvstore["❤️"]
        assert kvstore.get("❤️") is None

        with pytest.raises(KeyError):
            kvstore["baz"]

        with pytest.raises(KeyError):
            del kvstore["notakey"]

        with pytest.raises(KeyError):
            kvstore.pop("foo")


def test_serialize_deserialize(tmpdir):
    """Test serialize/deserialize"""
    dbpath = tmpdir / "kvtest.db"
    kvstore = sqlitekvstore.SQLiteKVStore(dbpath, serialize=json.dumps, deserialize=json.loads)
    kvstore.set("foo", {"bar": "baz"})
    assert kvstore.get("foo") == {"bar": "baz"}
    assert kvstore.get("FOOBAR") is None


def test_serialize_deserialize_binary_data(tmpdir):
    """Test serialize/deserialize with binary data"""
    dbpath = tmpdir / "kvtest.db"
    kvstore = sqlitekvstore.SQLiteKVStore(
        dbpath, serialize=pickle_and_zip, deserialize=unzip_and_unpickle
    )
    kvstore.set("foo", {"bar": "baz"})
    assert kvstore.get("foo") == {"bar": "baz"}
    assert kvstore.get("FOOBAR") is None


def test_serialize_deserialize_bad_callable(tmpdir):
    """Test serialize/deserialize with bad values"""
    dbpath = tmpdir / "kvtest.db"
    with pytest.raises(TypeError):
        sqlitekvstore.SQLiteKVStore(dbpath, serialize=1, deserialize=None)

    with pytest.raises(TypeError):
        sqlitekvstore.SQLiteKVStore(dbpath, serialize=None, deserialize=1)


def test_iter(tmpdir):
    """Test generator behavior"""
    dbpath = tmpdir / "kvtest.db"
    kvstore = sqlitekvstore.SQLiteKVStore(dbpath)
    kvstore.set("foo", "bar")
    kvstore.set("baz", "qux")
    kvstore.set("quux", "corge")
    kvstore.set("grault", "garply")
    assert len(kvstore) == 4
    assert sorted(iter(kvstore)) == ["baz", "foo", "grault", "quux"]


def test_keys_values_items(tmpdir):
    """Test keys, values, items"""
    dbpath = tmpdir / "kvtest.db"
    kvstore = sqlitekvstore.SQLiteKVStore(dbpath)
    kvstore.set("foo", "bar")
    kvstore.set("baz", "qux")
    kvstore.set("quux", "corge")
    kvstore.set("grault", "garply")
    assert sorted(kvstore.keys()) == ["baz", "foo", "grault", "quux"]
    assert sorted(kvstore.values()) == ["bar", "corge", "garply", "qux"]
    assert sorted(kvstore.items()) == [
        ("baz", "qux"),
        ("foo", "bar"),
        ("grault", "garply"),
        ("quux", "corge"),
    ]


def test_path(tmpdir):
    """Test path property"""
    dbpath = tmpdir / "kvtest.db"
    kvstore = sqlitekvstore.SQLiteKVStore(dbpath)
    assert kvstore.path == dbpath


def test_wipe(tmpdir):
    """Test wipe"""
    dbpath = tmpdir / "kvtest.db"
    kvstore = sqlitekvstore.SQLiteKVStore(dbpath)
    kvstore.set("foo", "bar")
    kvstore.set("baz", "qux")
    kvstore.set("quux", "corge")
    kvstore.set("grault", "garply")
    assert len(kvstore) == 4
    kvstore.wipe()
    assert len(kvstore) == 0
    assert "foo"
    kvstore.set("foo", "bar")
    assert kvstore.get("foo") == "bar"


def test_thread_safety_concurrent_writes(tmpdir):
    """Test that concurrent writes from multiple threads work correctly"""
    dbpath = tmpdir / "kvtest.db"
    kvstore = sqlitekvstore.SQLiteKVStore(dbpath)
    num_threads = 10
    writes_per_thread = 100
    errors = []

    def writer(thread_id):
        try:
            for i in range(writes_per_thread):
                key = f"thread_{thread_id}_key_{i}"
                value = f"thread_{thread_id}_value_{i}"
                kvstore.set(key, value)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Errors occurred: {errors}"
    assert len(kvstore) == num_threads * writes_per_thread

    # Verify all values are correct
    for thread_id in range(num_threads):
        for i in range(writes_per_thread):
            key = f"thread_{thread_id}_key_{i}"
            expected = f"thread_{thread_id}_value_{i}"
            assert kvstore.get(key) == expected

    kvstore.close()


def test_thread_safety_concurrent_reads_and_writes(tmpdir):
    """Test concurrent reads and writes from multiple threads"""
    dbpath = tmpdir / "kvtest.db"
    kvstore = sqlitekvstore.SQLiteKVStore(dbpath)

    # Pre-populate some data
    for i in range(100):
        kvstore.set(f"key_{i}", f"value_{i}")

    num_threads = 10
    operations_per_thread = 50
    errors = []

    def reader_writer(thread_id):
        try:
            for i in range(operations_per_thread):
                # Read existing key
                key_to_read = f"key_{i % 100}"
                kvstore.get(key_to_read)

                # Write new key
                new_key = f"thread_{thread_id}_new_{i}"
                kvstore.set(new_key, f"new_value_{i}")

                # Check if key exists
                _ = new_key in kvstore

                # Get length
                _ = len(kvstore)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=reader_writer, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Errors occurred: {errors}"
    kvstore.close()


def test_thread_safety_concurrent_set_many(tmpdir):
    """Test concurrent set_many operations"""
    dbpath = tmpdir / "kvtest.db"
    kvstore = sqlitekvstore.SQLiteKVStore(dbpath)
    num_threads = 5
    items_per_batch = 20
    errors = []

    def batch_writer(thread_id):
        try:
            items = [
                (f"batch_{thread_id}_key_{i}", f"batch_{thread_id}_value_{i}")
                for i in range(items_per_batch)
            ]
            kvstore.set_many(items)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=batch_writer, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Errors occurred: {errors}"
    assert len(kvstore) == num_threads * items_per_batch
    kvstore.close()


def test_thread_safety_concurrent_deletes(tmpdir):
    """Test concurrent delete operations"""
    dbpath = tmpdir / "kvtest.db"
    kvstore = sqlitekvstore.SQLiteKVStore(dbpath)

    # Pre-populate data
    num_keys = 100
    for i in range(num_keys):
        kvstore.set(f"key_{i}", f"value_{i}")

    errors = []
    deleted_keys = set()
    lock = threading.Lock()

    def deleter(start, end):
        try:
            for i in range(start, end):
                key = f"key_{i}"
                kvstore.delete(key)
                with lock:
                    deleted_keys.add(key)
        except Exception as e:
            errors.append(e)

    # Split deletions across threads
    num_threads = 4
    keys_per_thread = num_keys // num_threads
    threads = [
        threading.Thread(target=deleter, args=(i * keys_per_thread, (i + 1) * keys_per_thread))
        for i in range(num_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Errors occurred: {errors}"
    assert len(kvstore) == 0
    kvstore.close()


def test_thread_safety_iteration_during_writes(tmpdir):
    """Test that iteration works while other threads are writing"""
    dbpath = tmpdir / "kvtest.db"
    kvstore = sqlitekvstore.SQLiteKVStore(dbpath)

    # Pre-populate some data
    for i in range(50):
        kvstore.set(f"initial_{i}", f"value_{i}")

    errors = []

    def writer():
        try:
            for i in range(100):
                kvstore.set(f"new_key_{i}", f"new_value_{i}")
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            # Perform multiple iterations while writes are happening
            for _ in range(5):
                # Each of these operations should complete without error
                # Note: counts may differ between calls since writes happen concurrently
                list(kvstore.keys())
                list(kvstore.values())
                list(kvstore.items())
                len(kvstore)
        except Exception as e:
            errors.append(e)

    writer_thread = threading.Thread(target=writer)
    reader_thread = threading.Thread(target=reader)

    writer_thread.start()
    reader_thread.start()

    writer_thread.join()
    reader_thread.join()

    assert len(errors) == 0, f"Errors occurred: {errors}"
    # Verify final state: should have 50 initial + 100 new = 150 items
    assert len(kvstore) == 150

    kvstore.close()
