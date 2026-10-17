"""Close stores even when a test assertion fails."""

import pytest

from sqlitekvstore import SQLiteKVStore


@pytest.fixture(autouse=True)
def close_stores(monkeypatch):
    stores = []
    original = SQLiteKVStore.__init__

    def tracked(self, *args, **kwargs):
        original(self, *args, **kwargs)
        stores.append(self)

    monkeypatch.setattr(SQLiteKVStore, "__init__", tracked)
    yield
    for store in reversed(stores):
        store.close()
