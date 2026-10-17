"""Build-artifact and installed-type smoke checks, outside the source checkout.

Usage: uv run python scripts/check_distribution.py /path/to/built/artifacts
"""

import email
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMOKE = """
import sqlitekvstore
import sys
from pathlib import Path
from sqlitekvstore import SQLiteKVStore
assert Path(sqlitekvstore.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
with SQLiteKVStore('smoke.db', wal=True, synchronous='FULL') as store:
    store.set_many({'key': 'value', 'nullable': None})
with SQLiteKVStore('smoke.db') as store:
    assert store.pop('key') == 'value'
    assert store.get('nullable', 'missing') is None
    assert store.get('key', 'missing') == 'missing'
"""


def main():
    artifacts = Path(sys.argv[1]).resolve()
    wheels = list(artifacts.glob("*.whl"))
    sources = list(artifacts.glob("*.tar.gz"))
    assert len(wheels) == len(sources) == 1, "Use a directory containing one wheel and one sdist"
    with zipfile.ZipFile(wheels[0]) as archive:
        metadata = email.message_from_bytes(
            archive.read(
                next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
            )
        )
        assert "sqlitekvstore-stubs/__init__.pyi" in archive.namelist()
    assert metadata["Requires-Python"] == ">=3.12"
    assert metadata.get_all("Requires-Dist", []) == [], "Runtime must remain dependency-free"
    with tarfile.open(sources[0]) as archive:
        assert any(member.name.endswith("/sqlitekvstore.pyi") for member in archive.getmembers())
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("MYPYPATH", None)
    for artifact in [*wheels, *sources]:
        with tempfile.TemporaryDirectory(prefix="sqlitekvstore-install-") as temporary:
            work = Path(temporary)
            venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(work / "venv")
            python = work / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            subprocess.run(
                ["uv", "pip", "install", "--python", str(python), str(artifact)],
                cwd=work,
                env=environment,
                check=True,
                timeout=120,
            )
            subprocess.run(
                [str(python), "-c", SMOKE],
                cwd=work,
                env=environment,
                check=True,
                timeout=30,
            )
            shutil.copyfile(ROOT / "tests/typing_examples.py", work / "typing_examples.py")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mypy",
                    "--strict",
                    "--no-incremental",
                    "--python-executable",
                    str(python),
                    "typing_examples.py",
                ],
                cwd=work,
                env=environment,
                check=True,
                timeout=60,
            )
            print(f"{artifact.name}: metadata, persistence, and installed typing passed")


if __name__ == "__main__":
    main()
