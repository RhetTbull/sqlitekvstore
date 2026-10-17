# Developer Notes

These notes are to help me remember how to work on the project.

## Setup

Use Python 3.12 or newer. See [the robustness design spec](DESIGN.md) for the
implementation sequence, behavioral contracts, and acceptance criteria.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) (used to manage dependencies and publishing):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Install development dependencies:

```bash
uv sync --dev
```

## Testing

Format code and run linter:

```bash
uv run ruff format .
uv run ruff check --fix .
```

Run tests:

```bash
uv run pytest --cov
```

## Quality checks

```bash
uv run mypy sqlitekvstore.py
uv run mypy tests/typing_examples.py
uv run python -m mypy.stubtest sqlitekvstore
uv run pytest --cov=sqlitekvstore --cov-branch
uv build --out-dir artifacts
uv run python scripts/check_distribution.py artifacts
```

Use an empty artifact directory for the distribution check. It installs the wheel
and sdist into separate temporary environments and checks persistence and installed
annotations from outside the checkout. Keep `sqlitekvstore.pyi` aligned with the
runtime API; it provides type information for this single-module distribution.
The wheel installs the stub as `sqlitekvstore-stubs/__init__.pyi` so type checkers
can discover it in site-packages, following the [stub-package convention](https://peps.python.org/pep-0561/#stub-only-packages). The runtime implementation remains in
`sqlitekvstore.py`.

The test matrix covers Linux on Python 3.12–3.14 and Windows/macOS on 3.12 and
3.14. The quality job enforces lint, formatting, strict mypy, stub consistency,
100% statement/branch coverage, and built distribution checks. Repository
administrators should require these jobs in branch protection before release.

Tests that can deadlock run in bounded subprocesses. Coverage instruments those
subprocesses as well. Worker waits/joins have deadlines, spawned processes open
independent stores, and fixtures close stores before deleting temporary files.

## Thread safety and transactions

One lock protects each instance's connection. Compound mutations use explicit
SQLite transactions, so separate instances/processes also coordinate correctly.
Private helpers called under the lock never acquire it recursively. Callbacks
normally run outside the lock; `pop()` decodes inside its write transaction to
preserve entries on failure. Callbacks must be thread-safe and must not reenter
the store or wait on other operations on the database. Raw connections are
caller-managed. See README.md for the public lifetime and concurrency contracts.

Each mutation owns a `BEGIN IMMEDIATE` transaction and commits or rolls back before
returning. No helper adopts an external transaction. Rollback failure invalidates
the store, preserves the primary exception, and attaches cleanup context. The
connection explicitly uses legacy transaction control with implicit transactions
disabled; raw SQL requires an explicit BEGIN when grouping writes.

## Release

Build the package:

```bash
uv build
```

Publish to PyPI:

```bash
uv publish
```

## Version Bumping

Version is defined in two places that must be kept in sync:
- `sqlitekvstore.py` (`__version__`)
- `pyproject.toml` (`version`)

The `.bumpversion.cfg` file is configured to update both files:

```bash
bump2version patch  # 0.3.0 -> 0.3.1
bump2version minor  # 0.3.0 -> 0.4.0
bump2version major  # 0.3.0 -> 1.0.0
```
