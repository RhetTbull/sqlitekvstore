# Developer Notes

These notes are to help me remember how to work on the project.

## Setup

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

## Thread Safety

The `SQLiteKVStore` class is thread-safe. Multiple threads can safely read and write to the same store instance concurrently.

Implementation details:
- Uses `check_same_thread=False` when creating SQLite connections to allow multi-threaded access
- All database operations are protected by a `threading.Lock()` to serialize access
- Each method (`get`, `set`, `delete`, `keys`, `values`, `items`, etc.) is individually atomic
- Note: There is no snapshot isolation across separate method calls. If you call `keys()` then `values()` while another thread is writing, the counts may differ.

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
