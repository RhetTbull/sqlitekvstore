# SQLiteKVStore robustness design

Date: 2026-10-17  
Status: Implemented on `codex/robustness`; cross-platform execution is delegated to the configured CI matrix.  
Reviewed baseline: version 0.4.0, commit `2fbe97ecf063b6a2139c7231c892b48c17236147`.

## 1. Purpose and scope

Make SQLiteKVStore dependable for small persistent stores used by command-line
tools, applications with worker threads, and multiple processes accessing one
local database. Prioritize data integrity, predictable exceptions, and documented
concurrency behavior while preserving the single-file implementation and zero
runtime dependencies.

The minimum supported Python version is **3.12**. The initial CI matrix is Python
3.12, 3.13, and 3.14. New Python versions should be added deliberately after
compatibility testing; this document does not claim they have already been tested.

This design keeps the existing on-disk schema and existing positional constructor
arguments. It does not introduce a public transaction API, connection pool,
asynchronous interface, schema migration framework, or full `MutableMapping`
implementation. Streaming large databases, TTLs, atomic counters, and additional
dictionary methods are separate proposals.

The user authorized implementation of this spec on a new branch. The API and
compatibility recommendations collected in section 12 are adopted by that implementation.
Version 0.4.0 is retained pending release preparation; no release is published.

## 2. Review findings

The repository and GitHub `main` matched at review time. There were no open issues
or pull requests. The closed [issue #1: Make this thread-safe](https://github.com/RhetTbull/sqlitekvstore/issues/1)
introduced relevant work, but the compound-operation gaps below remain.

All 20 existing tests passed on Python 3.14.0 with SQLite 3.50.4, including 100%
statement and branch coverage. Ruff lint and formatting checks passed. These
numbers describe execution coverage, not completeness of behavioral testing.

| Finding | Evidence or consequence | Priority |
| --- | --- | --- |
| Failed batches leave partial work pending | Reproduced a valid insert followed by a null-key failure; a later unrelated write committed the valid insert | High |
| Failed commits leave writes pending | Reproduced a reader blocking a commit; a later write committed the earlier failed operation | High |
| `pop()` is not atomic | Reproduced a concurrent replacement being deleted while `pop()` returned the old value | High |
| Dictionary deletion checks and deletes separately | Code inspection shows a race between membership testing and deletion | High |
| `get()` swallows decoder `KeyError` | Reproduced an existing entry returning the default when decoding raised `KeyError` | Medium |
| Callback validation uses truthiness | Reproduced acceptance of `0`, `False`, empty strings, lists, and dictionaries | Medium |
| File existence substitutes for schema initialization | Reproduced an existing empty file opening successfully, then failing with `no such table: data` | Medium |
| Concurrency guarantees exceed the implementation | Raw connections bypass locking; callbacks run concurrently; tests omit same-key contention and multiple connections | Medium |
| Test and support claims lack enforcement | CI omitted the old minimum version; no type-checking step; `test_wipe()` contains `assert "foo"` | Medium |
| WAL also changes durability settings | `wal=True` sets `synchronous=NORMAL`, but the README describes only performance and extra files | Medium |

## 3. Required behavioral guarantees

1. Each successful mutation commits before returning. An ordinary statement,
   binding, callback, or commit failure rolls back that operation's changes.
2. Failed operations must not leave changes that another store method later
   commits. A connection that cannot be recovered is made unusable.
3. `set_many()` is all-or-nothing, including when replacing existing entries.
4. `pop()` returns the value belonging to the entry it removes. Concurrent
   operations must have an outcome consistent with some serial ordering.
5. `get(key, default)` returns `default` only when no matching row exists.
6. Basic public operations on a shared instance coordinate through one lock.
   Separate instances and processes coordinate through SQLite transactions.
7. Independent calls do not form a transaction: `get()` followed by `set()` is
   not an atomic read-modify-write, and `keys()` followed by `values()` is not a
   shared snapshot.
8. Existing valid databases remain readable without rewriting their schema.
9. Cleanup must preserve the original failure and release database resources.

These guarantees apply to supported package operations on a normal local SQLite
database. Raw-connection modifications, process-fork inheritance, external schema
changes, and broken storage hardware are outside that contract. In particular,
an I/O or rollback failure can leave the durable result uncertain; the package
must report the error and invalidate the connection rather than claim certainty.

## 4. Transaction and mutation design

### 4.1 Explicit transaction ownership

Create connections with `check_same_thread=False`, an explicit
`autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL`, and `isolation_level=None`.
Specifying both transaction settings avoids depending on a future Python default.
The package starts and completes its transactions explicitly. This also keeps
SQLite idle between operations so journal configuration and `VACUUM` are possible.
See the [Python 3.12 transaction-control reference](https://docs.python.org/3.12/library/sqlite3.html#transaction-control).

Use a small private transaction helper called only while the instance lock is
held. It must:

1. Verify that the instance is open and not invalidated.
2. Reject an already-active externally owned transaction with
   `sqlite3.ProgrammingError`; never commit or roll back someone else's work.
3. Execute `BEGIN IMMEDIATE` to acquire the writer reservation before any
   read-modify-write sequence.
4. Execute the operation, closing its cursors before completing the transaction.
5. Commit once, only after the entire operation succeeds.
6. On any `BaseException`, including interruptions, roll back if this helper
   successfully began a transaction and it is still active; then re-raise.

A failure to begin does not give the helper ownership of any transaction.
Do not retry an entire operation automatically: that can repeat callbacks or
iterator side effects. Let SQLite's configured busy timeout handle waiting.
SQLite's [transaction documentation](https://www.sqlite.org/lang_transaction.html)
explains writer reservations and why a busy commit may leave a transaction active.

If rollback fails, preserve the primary exception, attach cleanup context using
an exception note, and best-effort close/invalidate the connection. Subsequent
database operations must raise `sqlite3.ProgrammingError`. Do not continue using
a connection whose transaction state is uncertain. Do not log keys or values.

Keep the helper internal. Public context management still means automatic close,
not a transaction covering the entire `with SQLiteKVStore(...)` block.

### 4.2 Individual writes and batches

Route `set()`, `set_many()`, `delete()`, dictionary deletion, `pop()`, the `about`
setter, and the deletion phase of `wipe()` through the same transaction policy.
Use explicit column names in inserts. Retain current replacement semantics for
this release; an UPSERT migration is unnecessary for the integrity fixes.

For `set()` and `set_many()`, validate/materialize and serialize input before
starting the write transaction. Keep batch materialization for now so iterable
and serializer failures cannot leave partial database changes. This uses memory
proportional to the batch and must be documented. Accept mappings through
`collections.abc.Mapping` as well as iterables of pairs. Repeated keys in one batch
retain the current last-value-wins behavior. An empty batch is a no-op after the
open-state check.

Do not introduce restrictive runtime type validation that silently breaks SQLite
adapters. Let SQLite produce its existing binding, integrity, and overflow errors;
the transaction helper is responsible for cleanup. Validate only the package's
own configuration parameters explicitly.

### 4.3 Atomic deletion and pop

Implement `delete()` as one transactional `DELETE`. It remains a no-op for a
missing key. Implement `del store[key]` using the same statement and its affected
row count; raise `KeyError(key)` if no row was deleted. Do not perform a prior
membership check or decode a value merely to delete it.

Implement `pop()` as follows, under one instance lock and one write transaction:

1. Select the stored value for the key.
2. Raise `KeyError(key)` if the row is absent.
3. Deserialize the value before deletion.
4. Delete the matching row.
5. Commit, then return the decoded value.

If decoding or commit fails, the transaction rolls back and no value is returned.
Decoding failure leaves the entry intact. Holding the write reservation across
the read and delete also protects against other connections and processes.
Avoid requiring `DELETE ... RETURNING`, because Python's version alone does not
guarantee a particular linked SQLite version.

This deliberately holds a write transaction while decoding. A slow decoder can
delay other writers; this tradeoff preserves the entry when decoding fails.
Callbacks must be short and must not wait for another operation on this database.
Reentrancy handling is specified in section 5.2.

### 4.4 Wipe and maintenance

`VACUUM` cannot be part of the deletion transaction. Preserve `wipe()`'s current
automatic vacuum behavior, but add a proposed keyword-only `vacuum: bool = True`
option so callers can request only atomic deletion. Keep the instance lock across
both phases when vacuuming, using private helpers to avoid recursive lock access.

The deletion commits first. If vacuuming fails, propagate the SQLite exception
with a note that deletion already committed. Do not imply that the data was
restored. Another connection can write between deletion and vacuum; successful
`wipe()` promises an atomic clear at a point in time, not lasting emptiness while
other writers are active. Test the maintenance failure separately from deletion
failure. `vacuum()` itself requires an open connection with no active transaction.

## 5. Thread safety, callbacks, and resource lifetime

### 5.1 Locking model

Retain one `threading.Lock` per instance and one SQLite connection. Every public
database operation acquires this lock; private helpers used within a locked
operation must not reacquire it or call other public database methods. The GIL
is not part of the correctness argument.

Separate instances need no shared Python lock. SQLite coordinates their
transactions, including those belonging to other processes. Reject SQLite builds
reporting `sqlite3.threadsafety == 0` with a clear initialization error, since they
cannot support this class's sharing contract. Record Python and SQLite versions
in CI diagnostics. A free-threaded Python support claim requires a dedicated test
job and is outside the initial release gate.

Do not inherit an instance across `fork()`. Each child process must open its own
store; multiprocessing tests should use the spawn start method. This is a
documented boundary rather than an automatic reconnection feature.

### 5.2 Callback contract and error isolation

Validate serializer and deserializer independently: `None` disables the callback;
any other value must be callable. Invoke callable objects even when their boolean
value is false.

Retrieve raw rows with an internal missing sentinel or explicit `row is None`
check. `get()` returns its default before decoding only if the row is absent.
Decode errors, including `KeyError`, propagate unchanged. A stored SQL `NULL`
is a present value and must not be confused with a missing row.

Keep serialization for ordinary writes and decoding for reads/iteration outside
the database lock. Callbacks can therefore run concurrently on a shared instance
and must be thread-safe. `pop()` is the documented exception: decoding occurs
inside its transaction to preserve data on failure.

To prevent same-thread deadlock in `pop()`, add a per-instance, thread-local
callback guard. Set it around all package serializer/deserializer invocations and
clear it in `finally`. Public methods that access or close the database, including
`connection()`, must check the guard **before** acquiring the instance lock and
raise `sqlite3.ProgrammingError` on same-instance callback reentry. For generators,
perform this check when execution actually begins. Pure `path` access is allowed.

Apply this rule consistently to all callbacks, not just `pop()`. It is a proposed
compatibility restriction that must appear in release notes. A callback must also
not start another thread or connection and wait for it to operate on the same
database; the thread-local guard cannot detect arbitrary cross-thread wait cycles.
Externally registered SQLite adapters must obey the same non-reentry rule, though
the package cannot wrap every adapter invocation with this guard.

### 5.3 Close and initialization failure

Track an explicit open/closed or invalidated state. `close()` acquires the lock,
is idempotent, and coordinates with database operations already holding that
lock. A call that reaches the connection after close fails predictably with
`sqlite3.ProgrammingError`.

Serialization may overlap a close because it runs outside the lock. Check open
state before callback execution and again after acquiring the lock; if close won,
the write must fail without touching the database. A read that already copied its
raw result may finish decoding after close. An iterator that already fetched its
snapshot may finish yielding after close; an unstarted iterator fails on first
advance after close.

`__enter__()` checks that the store is open. `__exit__()` always attempts cleanup;
if both the body and cleanup fail, preserve the body's exception and attach the
cleanup failure as context. The destructor remains best-effort and must tolerate
partial construction; applications must use explicit close or context management.

### 5.4 Raw connection access

Retain `connection()` for compatibility, including returning the closed connection
after `close()` as current tests expect. State plainly that this returns an unsafe
escape hatch, not a lock-protected session.

Callers must arrange exclusive access, close cursors, finish transactions before
resuming store operations, and avoid changing schema, row factories, connection
transaction settings, or connection lifetime behind the store. Public database
operations should reject an active external transaction rather than adopt it.
Do not add a public connection context manager in this release.

Explicit transaction configuration changes raw SQL behavior: an external write
outside `BEGIN` commits immediately when implicit transactions are disabled.
Document this compatibility change with an explicit `BEGIN`/commit example.

## 6. Opening and validating databases

Remove the filesystem existence test. Always open the requested path through
SQLite, then inspect the schema. Support `str` and `os.PathLike[str]`, including
`pathlib.Path`, plus the existing `":memory:"` special path. Preserve the original
argument in `path` to avoid breaking callers that compare path objects.

Recommended opening procedure:

1. Validate configuration and initialize safe cleanup state.
2. Open a connection with explicit transaction and timeout settings.
3. Inspect the existing schema. For an already-valid store, avoid acquiring a
   writer reservation merely to open it.
4. If the database has no user objects, begin an immediate transaction, recheck
   the schema after acquiring the reservation, and create both tables and the
   existing index only if it is still empty. Another opener may have initialized
   it while this connection waited.
5. If either reserved table already exists, validate the complete store schema.
   Reject partial or incompatible stores without attempting repair. Reject an
   unrelated nonempty database with no store tables.
6. Apply requested journal/durability configuration outside any transaction.
7. Publish the fully initialized connection on the instance.

Validate structure using SQLite schema metadata rather than literal CREATE SQL
text. The `data` table must have the expected `key` and `value` columns, compatible
BLOB declarations, and a non-null unique primary key. `_about` must have the
expected integer primary key and text description columns. Reject extra columns
in these reserved tables for this release. Preserve unrelated tables and extra
indexes in an otherwise valid store; do not require the redundant named `idx_key`
index if the primary-key constraint already provides uniqueness. New stores keep
the existing schema, including that index, to avoid unrelated format changes.

Raise `sqlite3.DatabaseError` with a concise schema explanation for a structurally
incompatible store. Preserve native errors for corrupt files, inaccessible paths,
and missing parent directories. Do not create parent directories automatically.
Schema checks are not a replacement for a full integrity check; do not run
`PRAGMA integrity_check` on every open.

Every construction failure closes its local connection explicitly. Schema
creation is transactional. Do not unlink files on failure: another process might
already use the path. If journal configuration fails after schema creation commits,
a valid empty database may remain; document and test this outcome.

## 7. Configuration and durability

Proposed constructor shape, retaining the existing four positional arguments:

```python
SQLiteKVStore(
    dbpath,
    serialize=None,
    deserialize=None,
    wal=False,
    *,
    timeout=5.0,
    synchronous=None,
)
```

`timeout` is the SQLite lock-wait timeout in seconds. Accept finite nonnegative
real numbers, reject booleans and nonnumeric values with `TypeError`, and reject
negative/nonfinite values with `ValueError`. Zero requests immediate failure on
contention. It is not an overall deadline for a method: Python lock acquisition,
callbacks, and multiple SQL statements can take additional time.

The proposed `synchronous` option accepts only `None`, `"NORMAL"`, `"FULL"`, or
`"EXTRA"`. Validate against a fixed allowlist before constructing a PRAGMA.
Its default preserves current behavior: explicit `wal=True` selects NORMAL;
otherwise the package does not override SQLite's connection setting. An explicit
option overrides this behavior. Do not expose OFF in this robustness pass.

`wal=False` continues to mean "do not request a journal-mode change"; it does not
disable WAL already persisted in the database. When `wal=True`, inspect the mode
returned by SQLite. For file-backed stores, inability to activate WAL should raise
a clear `sqlite3.OperationalError`. In-memory stores remain supported and may
report the MEMORY journal mode; document this exception. Synchronous configuration
is connection-specific and must be applied on each open when explicitly requested.

Document that WAL plus NORMAL protects consistency but may lose recent commits
after power failure or an operating-system crash. Users who need stronger WAL
durability can choose FULL. Do not describe process-termination tests as proof of
power-loss durability. See [SQLite's synchronous documentation](https://www.sqlite.org/pragma.html#pragma_synchronous).

Keep durability configuration separate from the first integrity fix so transaction
recovery can land without settling performance defaults. This spec recommends
preserving the old default and making the tradeoff explicit.

## 8. Public semantics and typing

| API | Required behavior |
| --- | --- |
| `get(key, default)` | Default only for an absent row; decoder errors propagate |
| `store[key]` | `KeyError(key)` for absence; stored null remains a valid value |
| `delete(key)` | Missing key is a successful no-op |
| `del store[key]` | Missing key raises `KeyError(key)` without a separate existence check |
| `pop(key)` | Atomic decode-and-remove; missing key raises; decoding failure preserves entry |
| `set_many(items)` | One commit or rollback for the whole batch; last repeated key wins |
| `keys()`, `values()`, `items()` | Materialized per-iteration snapshot, taken on first advance |
| `close()` | Idempotent; subsequent database operations fail predictably |
| Context manager | Closes on exit; does not group operations into one transaction |
| `wipe(vacuum=True)` | Atomic deletion followed by separately fallible maintenance |

Iteration order remains unspecified. A snapshot contains raw SQLite values; user
decoding happens as items are yielded. A decoding failure may interrupt iteration
after earlier items were already delivered. No snapshot is shared between separate
calls, and memory consumption is proportional to the selected result set.

Replace the single reused `TypeVar` with annotations that distinguish keys, raw
SQLite values, application values, and defaults. Prefer an honest non-generic
public class with `Any` at adapter/callback boundaries over a generic promise the
runtime cannot enforce. Use precise return types for `None`, `bool`, `int`,
iterators, path properties, and `sqlite3.Connection`, and `Self` for context entry.
Use `Mapping`/`Iterable` in batch input annotations. Do not claim that one type
variable describes both a JSON-serializable dictionary and its stored string.

Add mypy as a development dependency and enforce checking of the implementation,
with small caller examples for JSON, pickle, path objects, and heterogeneous keys.
Verify that the built distribution makes annotations available to type checkers;
include appropriate typing metadata if required for the chosen single-module
packaging. Replace "100% mypy type checking" with the exact enforced check.

Keep SQLite's exception hierarchy and original callback exceptions. New custom
exceptions are not necessary for these changes. Failure messages and notes should
identify the operation without exposing stored values. Do not silently return
defaults, retry mutations, or wrap every SQLite error in one generic exception.

## 9. Test and CI design

### 9.1 Required regression cases

| Area | Cases and assertions |
| --- | --- |
| Transaction recovery | Failure after a valid batch item; replacement rolled back; invalid key/value binding; integer overflow; commit blocked by a reader; subsequent successful operation must not persist failed work |
| Cleanup | Begin failure; statement failure; commit failure; rollback failure invalidates connection; interruption cleanup preserves the original exception |
| Atomic pop | Two consumers of one key produce one value and one missing-key error; competing replacement is never silently removed with an old return value; decode failure leaves data unchanged |
| Deletion | Competing dictionary deletes yield one success and one missing-key error; `delete()` remains idempotent; deletion never invokes the decoder |
| Callbacks | Serializer and decoder exceptions including `KeyError`; false-valued callable; each false-valued non-callable; reentry rejected without deadlock; callback guard cleared after errors |
| Opening | New path; existing empty file; normal reopen; in-memory store; path object; concurrent first opens; partial schema; incompatible schema; unrelated database; corrupt file; missing directory; initialization cleanup |
| Lifecycle | Double close; operations after close; context-body exception; concurrent close/write; snapshot consumed after close; unstarted iterator after close |
| Contention | Same instance, multiple instances, spawned processes; timeout and subsequent recovery; WAL and default journal mode |
| Values and keys | Empty string and bytes; Unicode; SQL-looking strings; integer boundaries; finite floats; null values; invalid null/NaN keys; document SQLite numeric-key equality rather than Python dictionary assumptions |
| Maintenance | Empty and populated wipe; metadata retained; vacuum disabled; vacuum failure after committed deletion; no open transaction left behind |
| Configuration | Default and explicit timeouts; invalid options; requested/effective WAL mode; synchronous overrides; existing WAL reopened with `wal=False` |
| Packaging | Wheel/sdist build, correct Python requirement, clean-environment installation, import and persistence smoke test outside the checkout |

Use real SQLite connections for binding failures and lock contention. For a
deterministic commit failure in default journal mode, hold an explicit read
transaction on a separate connection, let the store write, and use a short busy
timeout to fail its commit. Release the reader and verify rollback and recovery
using a separate connection and a reopen. WAL readers behave differently; do not
reuse this scenario as a WAL commit-failure test.

Use small private seams or connection wrappers for hard-to-trigger rollback and
close failures. Test externally meaningful cleanup and exception behavior, not
the number or names of internal helper calls. Do not add filesystem-permission
tests that pass or fail depending on whether CI runs as an administrator; use
portable database conditions or narrowly scoped fault injection.

### 9.2 Deterministic concurrency tests

Use barriers, events, or queues to establish the necessary operation ordering.
Do not depend on arbitrary sleeps or repeated probabilistic stress loops to
reproduce a race. In an atomic implementation, verify a competing writer remains
blocked until the transaction completes, then assert a valid final ordering.

Capture all worker exceptions and propagate them to the test. Every event wait,
thread join, process join, and subprocess has a bounded deadline. Release gates in
`finally` blocks. Run deadlock-sensitive scenarios in a subprocess so a hung
non-daemon worker cannot prevent pytest from exiting. Terminate/join failed child
processes and close every store before removing temporary directories.

Add regression tests alongside each fix rather than postponing them to a testing
phase. Fix the always-true wipe assertion and use managed fixtures consistently.

### 9.3 CI matrix and gates

The immediate baseline change removes Python 3.9–3.11 from package support and
sets Ruff's target to Python 3.12. The full proposed matrix is:

| OS | Python versions | Checks |
| --- | --- | --- |
| Linux | 3.12, 3.13, 3.14 | Full tests, including multiprocessing and journal variants |
| Windows | 3.12, 3.14 | Full tests, emphasizing file cleanup and process spawning |
| macOS | 3.12, 3.14 | Full tests, emphasizing SQLite runtime differences |

Run lint, formatting, mypy, branch coverage, and build/install verification in
dedicated jobs or on one Linux matrix entry to avoid unnecessary duplication.
Use a locked dependency install and explicitly select each matrix interpreter.
Print Python/SQLite versions and threading mode for diagnostics. Make the jobs
required before release.

Keep 100% statement and branch coverage as the intended gate for this small module.
Do not use exclusions to hide meaningful failure branches. Any unavoidable
platform-specific exclusion needs an explanation and an alternative validation.
The regression matrix remains the primary correctness criterion; a coverage
percentage alone does not demonstrate that an operation is atomic.

## 10. Recommended sequencing and proposed issues

The identifiers below are planning labels, not existing GitHub issue numbers.
Do not create or close GitHub issues merely by updating this document.

| Order | Proposed issue / change | Dependencies | Completion criteria |
| --- | --- | --- | --- |
| 0 | **BASE: Require Python 3.12** | None | Metadata, classifiers, lockfile, README, Ruff target, and existing CI agree; baseline tests run on 3.12; included with this document |
| 1 | **TXN: Recover every failed mutation** | BASE | Explicit transaction helper; every mutation uses commit/rollback discipline; partial batch and failed-commit regressions pass; no failed write leaks into a later commit |
| 2 | **ATOMIC: Make pop and dictionary deletion atomic; preserve callback errors** | TXN | Same-key and cross-connection race tests pass; callback validation and missing-value logic corrected; reentry fails promptly; pop decode failure preserves entry |
| 3 | **OPEN: Validate schema and manage connection lifetime** | TXN, ATOMIC callback guard | Empty-file and concurrent-open cases work; incompatible schema rejected without modification; failed construction closes resources; close behavior and raw-connection contract tested |
| 4 | **CONFIG: Expose contention/durability settings and clarify maintenance** | TXN, OPEN | Validated timeout and synchronous options; effective WAL behavior checked; optional wipe vacuum and post-delete maintenance errors tested |
| 5 | **QUALITY: Expand CI, typing, and published contracts** | BASE; final integration after 1–4 | OS/version matrix green; type checking and artifact smoke tests enforced; docs and release notes accurately describe guarantees |

Implement and review these as separate changes in the listed order. TXN can land
without waiting for durability API decisions. ATOMIC keeps callback and pop changes
together because safe decoding and lock behavior are inseparable. QUALITY work
that establishes infrastructure can start earlier, but it cannot replace tests
and documentation delivered with each preceding change.

Suggested checkpoints:

1. **Integrity checkpoint after TXN and ATOMIC:** failed operations are recoverable,
   pop/delete are atomic, and all reproductions from the review have regression
   coverage except initialization.
2. **General-use checkpoint after OPEN and CONFIG:** initialization, resource
   lifetime, contention, and maintenance have explicit and tested contracts.
3. **Release checkpoint after QUALITY:** documentation, artifacts, supported
   platforms, and compatibility notes agree with the implementation.

Do not combine the correctness work with index removal, broad performance tuning,
schema changes, or a new transaction API. Measure bulk-write and iteration costs
only to detect a material regression, not to create a separate benchmark project.

## 11. Compatibility and release acceptance

The planned changes require Python 3.12 or newer. Existing installations on older
Python versions can remain on a compatible prior release. Update project metadata,
lockfile, classifiers, lint/type-check targets, installation instructions, and CI
together; do not merely remove older versions from testing.

Retain method names, existing positional arguments, default per-call persistence,
missing-key distinctions, stored-value formats, and valid 0.4.0 database schemas.
The new options are keyword-only. Preserve WAL's current default tradeoff unless
a separate review explicitly changes it.

Call out these changes in release notes:

- Python 3.9–3.11 support is removed.
- Failed writes now roll back instead of being committed by a later call.
- Decoder `KeyError` now propagates from `get()` for an existing row.
- Invalid false-valued callbacks now raise `TypeError`.
- Callback reentry into the same store is rejected; callbacks must not wait on
  another operation on the same database.
- Incompatible or partial schemas fail during construction instead of later use.
- Raw writes without an explicit transaction commit immediately under the proposed
  connection settings; raw-connection users must review their transaction usage.
- File-backed `wal=True` requests that cannot activate WAL now fail explicitly.

Recommend a 0.5.0 release for the combined support and behavior changes, with the
actual version bump performed at release preparation. This document and the
baseline-only change do not bump or publish a release.

Release acceptance requires all regression cases above, CI on the supported
matrix, a wheel and sdist install smoke test, reopening a database created with
0.4.0, and review of the documented exception/concurrency contracts. Verify that
older code can still read a database written by the new implementation when it
uses the unchanged schema and compatible serializers. Do not claim physical
power-loss testing or arbitrary network-filesystem safety from these checks.

## 12. Adopted design decisions

Python 3.12 is the minimum. The implementation adopts the following recommendations:

| Decision | Recommendation | Tradeoff |
| --- | --- | --- |
| Transaction control | Explicit transactions with pinned legacy-control mode and implicit transactions disabled | Predictable ownership, but changes raw SQL transaction defaults |
| Pop decode failure | Decode inside the write transaction and preserve the entry on failure | Slow decoders hold a writer reservation |
| Callback reentry | Reject same-instance reentry with a clear error | Prevents deadlocks but tightens an undocumented behavior |
| Existing schema policy | Initialize empty databases; reject partial/incompatible/unrelated databases | Avoids silent repair; users with unusual schemas need an explicit migration |
| Raw connection API | Keep as a documented caller-managed escape hatch | Preserves access without promising synchronization the API cannot enforce |
| WAL durability | Keep current defaults; add explicit synchronous control | Avoids a surprise performance change while making durability selectable |
| Wipe maintenance | Keep automatic vacuum by default and add a keyword to disable it | Preserves behavior while exposing separately fallible maintenance |
| Public transactions and streaming | Defer | Keeps this release focused on correctness and recovery |

## 13. Implementation validation

Implementation is complete on `codex/robustness`, with version 0.4.0 retained for
release preparation. The BASE, TXN, ATOMIC, OPEN, CONFIG, and QUALITY work items
above are implemented. Local validation on macOS completed on 2026-10-17:

- 190 tests pass on Python 3.12.9, 3.13.2, and 3.14.0, with 100% statement and
  branch coverage on each interpreter. Deadlock-sensitive tests run in bounded
  subprocesses; multiprocessing tests use spawn and independent connections.
- Ruff lint and formatting, strict mypy implementation/caller checks, stub/runtime
  consistency, locked dependency validation, and diff whitespace checks pass.
- Wheel and sdist metadata, clean-environment installation, persistence, and
  installed caller type checks pass, including on Python 3.12. The wheel bundles
  the companion stub as `sqlitekvstore-stubs/__init__.pyi` so installed type
  checkers discover it while the runtime remains a single module.
- The actual implementation from baseline commit `2fbe97e` successfully reads
  JSON values written by the new implementation, and the new implementation
  successfully reads values written by the baseline.
- Schema validation uses the compatible `sqlite_master` name and falls back to
  `table_info` on builds without `table_xinfo`; modern generated columns are still
  rejected. The fallback is covered by a simulated older SQLite response.

The Linux and Windows matrix jobs are configured but have not been executed
locally. Repository administrators must enable the required checks in branch
protection before release; this implementation does not change repository access
settings. The configured CI run and release version bump remain release tasks.

## References

- [Repository](https://github.com/RhetTbull/sqlitekvstore)
- [Closed thread-safety issue #1](https://github.com/RhetTbull/sqlitekvstore/issues/1)
- [Python 3.12 sqlite3 documentation](https://docs.python.org/3.12/library/sqlite3.html)
- [SQLite transaction semantics](https://www.sqlite.org/lang_transaction.html)
- [SQLite synchronous settings](https://www.sqlite.org/pragma.html#pragma_synchronous)
- [SQLite VACUUM](https://www.sqlite.org/lang_vacuum.html)
