"""Packaging tests: the built distributions install cleanly and contain the right files.

Marked ``packaging`` and excluded from the default run (each builds a wheel+sdist and one test
installs into a throwaway venv). Run explicitly: ``pytest -m packaging``. The release pipeline
(P2-E) and CI (P2-C) run these against the same artifacts they publish.
"""

from __future__ import annotations

import subprocess
import sys
import tarfile
import venv
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.packaging

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def dists(tmp_path_factory) -> dict[str, Path]:
    """Build the wheel and sdist once into a temp dir; return their paths."""
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(out), str(_ROOT)],
        check=True,
        capture_output=True,
    )
    wheel = next(out.glob("*.whl"))
    sdist = next(out.glob("*.tar.gz"))
    return {"wheel": wheel, "sdist": sdist, "outdir": out}


def _expected_version() -> str:
    ns: dict[str, str] = {}
    exec((_ROOT / "src" / "focus_data_toolkit" / "_version.py").read_text(), ns)  # noqa: S102
    return ns["__version__"]


def test_version_is_single_sourced(dists):
    version = _expected_version()
    assert dists["wheel"].name == f"focus_data_toolkit-{version}-py3-none-any.whl"
    assert dists["sdist"].name == f"focus_data_toolkit-{version}.tar.gz"


def test_wheel_contents(dists):
    names = zipfile.ZipFile(dists["wheel"]).namelist()
    assert any(n.endswith("focus_data_toolkit/py.typed") for n in names), "py.typed missing"
    assert sum(1 for n in names if "/model/" in n and n.endswith(".json")) == 4, "model JSON missing"
    assert any(n.endswith("cli.py") for n in names)
    # No tests, no scratch, no golden fixtures leak into the wheel.
    assert not any("/tests/" in n or n.startswith("tests/") for n in names)


def test_sdist_contents(dists):
    names = tarfile.open(dists["sdist"]).getnames()
    assert any(n.endswith("/LICENSE") for n in names)
    assert any(n.endswith("/CHANGELOG.md") for n in names)
    # The model extractor ships in the sdist so the embedded model is reproducible from source.
    assert any(n.endswith("tools/extract_focus_1_4_model.py") for n in names)
    assert any(n.endswith("focus_data_toolkit/py.typed") for n in names)


def test_wheel_metadata(dists):
    z = zipfile.ZipFile(dists["wheel"])
    meta = z.read(next(n for n in z.namelist() if n.endswith("METADATA"))).decode()

    def field(name: str) -> str | None:
        return next((ln.split(":", 1)[1].strip() for ln in meta.splitlines() if ln.startswith(f"{name}:")), None)

    assert field("Version") == _expected_version()
    assert field("License-Expression") == "MIT"  # PEP 639
    assert field("License-File") == "LICENSE"
    assert field("Author") == "Guy-Hermann Adiko"
    assert field("Requires-Python") == ">=3.11"
    assert "Typing :: Typed" in meta
    assert "License :: OSI Approved" not in meta  # legacy classifier removed
    # The validator extra points at PyPI (not a git URL) and is Python-gated.
    assert any(
        'focus-validator' in ln and 'git+' not in ln and 'python_version >= "3.12"' in ln
        for ln in meta.splitlines()
        if ln.startswith("Requires-Dist:")
    )


def test_clean_install_smoke(dists, tmp_path):
    """Install the wheel into a throwaway venv and exercise the public surface."""
    env_dir = tmp_path / "venv"
    venv.create(env_dir, with_pip=True)
    py = env_dir / ("Scripts" if sys.platform == "win32" else "bin") / "python"
    subprocess.run([str(py), "-m", "pip", "install", "-q", str(dists["wheel"])], check=True, capture_output=True)

    script = (
        "import importlib.util, focus_data_toolkit as f;"
        f"assert f.__version__ == {_expected_version()!r}, f.__version__;"
        "assert len(f.__all__) >= 16;"
        "assert importlib.util.find_spec('pyarrow') is None, 'core pulled an optional dep';"
        "from focus_data_toolkit.generators import get_generator;"
        "assert get_generator('aws','1.3').generate_csv_bytes(10, 7);"
        "print('ok')"
    )
    out = subprocess.run([str(py), "-c", script], check=True, capture_output=True, text=True)
    assert out.stdout.strip() == "ok"

    # Console entry point is installed and runnable.
    ft = env_dir / ("Scripts" if sys.platform == "win32" else "bin") / "focus-toolkit"
    help_out = subprocess.run([str(ft), "--help"], check=True, capture_output=True, text=True)
    assert "generate" in help_out.stdout and "convert" in help_out.stdout
