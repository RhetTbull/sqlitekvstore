"""Run deadlock-sensitive checks in a bounded subprocess, not pytest's process."""

import json
import multiprocessing
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlitekvstore import SQLiteKVStore


def run_threads(functions):
    with ThreadPoolExecutor(max_workers=len(functions)) as pool:
        futures = [pool.submit(function) for function in functions]
        return [future.result(timeout=10) for future in futures]


def pop_races(path, wal):
    with SQLiteKVStore(path, wal=wal) as first, SQLiteKVStore(path, timeout=0) as other:
        for shared in [True, False]:
            second = first if shared else other
            for delete in [False, True]:
                first["key"] = "value"
                gate = threading.Barrier(2, timeout=5)

                def consume(store):
                    gate.wait()
                    try:
                        if delete:
                            del store["key"]
                            return "deleted"
                        return store.pop("key")
                    except KeyError:
                        return "missing"
                    except sqlite3.OperationalError:
                        # timeout=0 legitimately rejects another writer; retry after join.
                        return "busy"

                outcomes = run_threads([lambda: consume(first), lambda: consume(second)])
                if "busy" in outcomes:
                    assert "key" not in first
                else:
                    assert sorted(outcomes) == sorted(["deleted" if delete else "value", "missing"])
                assert "key" not in first

        entered = threading.Event()
        release = threading.Event()

        def decode(value):
            entered.set()
            assert release.wait(5)
            return value

        first._deserialize_func = decode
        first["key"] = "old"
        with ThreadPoolExecutor(max_workers=1) as pool:
            pop = pool.submit(first.pop, "key")
            try:
                assert entered.wait(5)
                with_error = False
                try:
                    other["key"] = "replacement"
                except sqlite3.OperationalError:
                    with_error = True
                assert with_error, "pop must hold a SQLite writer reservation while decoding"
            finally:
                release.set()
            assert pop.result(timeout=5) == "old"
        other["key"] = "replacement"
        first._deserialize_func = None
        assert first["key"] == "replacement"

        first["key"] = "old"
        entered.clear()
        release.clear()
        first._deserialize_func = decode
        attempted = threading.Event()

        def replace():
            attempted.set()
            first["key"] = "new"

        with ThreadPoolExecutor(max_workers=2) as pool:
            pop = pool.submit(first.pop, "key")
            assert entered.wait(5)
            replacement = pool.submit(replace)
            try:
                assert attempted.wait(5)
            finally:
                release.set()
            assert pop.result(timeout=5) == "old"
            replacement.result(timeout=5)
        first._deserialize_func = None
        assert first["key"] == "new"


def callback_reentry(path, wal):
    with SQLiteKVStore(path, wal=wal) as store:
        store["key"] = "value"
        operations = [
            lambda: store.get("key"),
            lambda: store.set("other", 1),
            lambda: store.set_many([]),
            lambda: store.delete("key"),
            lambda: store.pop("key"),
            lambda: store.__delitem__("key"),
            lambda: store["key"],
            lambda: len(store),
            lambda: "key" in store,
            lambda: list(store.keys()),
            lambda: list(store.values()),
            lambda: list(store.items()),
            lambda: store.about,
            lambda: setattr(store, "about", "x"),
            lambda: store.wipe(),
            lambda: store.vacuum(),
            lambda: store.connection(),
            lambda: store.close(),
            lambda: store.__enter__(),
        ]
        for operation in operations:

            def callback(value):
                assert store.path == path
                operation()
                raise AssertionError("reentry must fail")

            for mode in ["serialize", "get", "pop", "values", "items"]:
                store._serialize_func = callback if mode == "serialize" else None
                store._deserialize_func = None if mode == "serialize" else callback
                try:
                    if mode == "serialize":
                        store.set("other", 1)
                    elif mode == "get":
                        store.get("key")
                    elif mode == "pop":
                        store.pop("key")
                    else:
                        list(getattr(store, mode)())
                except sqlite3.ProgrammingError as error:
                    assert "reenter" in str(error)
                else:
                    raise AssertionError("callback did not fail")
                assert not store.connection().in_transaction
                assert "key" in store
        store._serialize_func = None
        store._deserialize_func = None
        assert store["key"] == "value"


def close_races(path, wal):
    for operation in ["set", "set_many", "get", "pop"]:
        entered = threading.Event()
        release = threading.Event()

        def callback(value):
            entered.set()
            assert release.wait(5)
            return value

        store = SQLiteKVStore(path, wal=wal)
        try:
            store["key"] = "value"
            if operation in ["set", "set_many"]:
                store._serialize_func = callback
            else:
                store._deserialize_func = callback
            with ThreadPoolExecutor(max_workers=2) as pool:
                if operation == "set":
                    future = pool.submit(store.set, "new", 1)
                elif operation == "set_many":
                    future = pool.submit(store.set_many, [("new", 1)])
                else:
                    future = pool.submit(getattr(store, operation), "key")
                assert entered.wait(5)
                try:
                    if operation == "pop":
                        closer = pool.submit(store.close)
                    else:
                        store.close()
                finally:
                    release.set()
                if operation in ["set", "set_many"]:
                    try:
                        future.result(timeout=5)
                    except sqlite3.ProgrammingError:
                        pass
                    else:
                        raise AssertionError("write after close succeeded")
                else:
                    assert future.result(timeout=5) == "value"
                if operation == "pop":
                    closer.result(timeout=5)
            with SQLiteKVStore(path) as check:
                assert "new" not in check
        finally:
            release.set()
            store.close()


def open_race(path, wal):
    gate = threading.Barrier(2, timeout=5)
    local = threading.local()

    class RacingStore(SQLiteKVStore):
        @classmethod
        def _schema_empty(cls, conn):
            empty = super()._schema_empty(conn)
            if not getattr(local, "checked", False):
                local.checked = True
                assert empty
                gate.wait()
            return empty

    def open_store():
        with RacingStore(path, wal=wal) as store:
            store[threading.get_ident()] = "value"

    run_threads([open_store, open_store])
    with SQLiteKVStore(path) as check:
        assert len(check) == 2


def process_worker(path, wal, number, gate):
    gate.wait(10)
    with SQLiteKVStore(path, wal=wal) as store:
        store.set_many([(f"{number}:{i}", i) for i in range(20)])
        try:
            store.pop("shared")
        except KeyError:
            pass


def process_writes(path, wal):
    with SQLiteKVStore(path, wal=wal) as store:
        store["shared"] = 1
    context = multiprocessing.get_context("spawn")
    gate = context.Barrier(3, timeout=10)
    processes = [
        context.Process(target=process_worker, args=(path, wal, i, gate)) for i in range(3)
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(15)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(5)
            process.close()
    with SQLiteKVStore(path) as check:
        assert len(check) == 60
        for i in range(3):
            for j in range(20):
                assert check[f"{i}:{j}"] == j


if __name__ == "__main__":
    globals()[sys.argv[1]](sys.argv[2], json.loads(sys.argv[3]))
