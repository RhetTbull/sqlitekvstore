# Developer Notes

These notes are to help me remember how to work on the project.

## Setup

Install poetry (used to manage development dependencies):

```bash
pip install poetry
```

Install development dependencies:

```bash
poetry install
```

## Testing

* `black sqlitekvstore.py test_sqlitekvstore.py`
* `flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics`
* `flake8 . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics`
* `poetry run pytest --mypy --cov --doctest-glob="README.md"`

## Release

Package management is done with [flit]](https://flit.readthedocs.io/en/latest/).

```bash
flit publish
```
