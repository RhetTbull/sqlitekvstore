# Changelog

## Unreleased

### Compatibility

- Require Python 3.12 or newer. Older interpreters can use an earlier compatible release.
- Use explicit per-operation transactions. Raw SQL outside an explicit `BEGIN` now
  commits immediately; raw-connection callers should review their transaction usage.
- `get()` propagates decoder errors, including `KeyError`, for existing rows.
- Reject false-valued non-callable serializer/deserializer arguments.
- Reject callback reentry into the same store. Callbacks must be thread-safe and
  must not wait for another operation on the same database.
- Validate schemas during construction; reject partial, unrelated, and incompatible
  databases instead of failing during later operations.
- Fail explicitly when a file-backed `wal=True` request cannot activate WAL.

### Reliability and API

- Roll back failed mutations and batches, including commit failures and interruptions.
  Invalidate connections if transaction recovery fails, preserving the primary exception.
- Make `pop()` and dictionary deletion atomic across threads, connections, and processes.
  A decoder failure during `pop()` preserves the entry.
- Initialize empty database files and coordinate concurrent first opens.
- Close failed constructor connections and preserve context-body exceptions on cleanup failure.
- Add keyword-only `timeout` and `synchronous` constructor settings.
- Add `wipe(vacuum=False)`; default vacuum failures report that deletion already committed.
- Accept general mappings in `set_many()` and preserve last-value-wins batch semantics.
- Ship public type stubs and enforce strict type checks and stub/runtime consistency.
- Add deterministic failure/concurrency tests, full branch coverage enforcement,
  Linux/Windows/macOS CI, and isolated wheel/sdist install checks.

The database format remains compatible with 0.4.0. WAL retains the historical
NORMAL synchronous default; select FULL explicitly for stronger power-loss durability.
