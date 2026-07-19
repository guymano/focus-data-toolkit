"""Unit tests for the official focus-validator subprocess wrapper.

The wrapper never had coverage: it is exercised here with a fake ``focus-validator``
executable so the tests need neither the real (Python >= 3.12 only) package nor the
network, and they run identically on 3.11 — where the ``[validator]`` extra is a
documented no-op and the wrapper must fail with an actionable message instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from focus_data_toolkit.official_validator import (
    OfficialValidatorNotInstalled,
    _executable,
    run_official_validator,
)


def _make_fake_validator(directory: Path, exit_code: int) -> tuple[Path, Path]:
    """Drop a fake ``focus-validator`` into ``directory``; return (exe, argv record)."""
    record = directory / "argv.txt"
    if sys.platform == "win32":
        exe = directory / "focus-validator.bat"
        exe.write_text(f'@echo off\necho %* > "{record}"\nexit /b {exit_code}\n')
    else:
        exe = directory / "focus-validator"
        exe.write_text(f'#!/bin/sh\necho "$@" > "{record}"\nexit {exit_code}\n')
        exe.chmod(0o755)
    return exe, record


def test_missing_validator_raises_with_install_hint(monkeypatch, tmp_path):
    # Point the interpreter-adjacent lookup at an empty directory and make the PATH
    # fallback fail, i.e. the state of every install without the [validator] extra
    # (including all of Python 3.11, where the extra is an intentional no-op).
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "python"))
    monkeypatch.setattr("focus_data_toolkit.official_validator.shutil.which", lambda _: None)
    with pytest.raises(OfficialValidatorNotInstalled) as exc:
        _executable()
    message = str(exc.value)
    assert "focus-data-toolkit[validator]" in message
    assert "3.12" in message


def test_executable_prefers_interpreter_bin_dir(monkeypatch, tmp_path):
    # A venv-local focus-validator must win over PATH, so the wrapper works when the
    # CLI itself was invoked by absolute path and the venv is not on PATH.
    bin_dir = tmp_path / "venv-bin"
    bin_dir.mkdir()
    name = "focus-validator.exe" if sys.platform == "win32" else "focus-validator"
    (bin_dir / name).touch()
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    monkeypatch.setattr(
        "focus_data_toolkit.official_validator.shutil.which",
        lambda _: pytest.fail("PATH lookup must not be reached"),
    )
    assert _executable() == str(bin_dir / name)


@pytest.mark.parametrize("exit_code", [0, 1, 7])
def test_run_invokes_validator_and_passes_through_exit_code(monkeypatch, tmp_path, exit_code):
    exe, record = _make_fake_validator(tmp_path, exit_code)
    # Keep the interpreter-adjacent lookup empty so the PATH fallback (the fake) is used.
    monkeypatch.setattr(sys, "executable", str(tmp_path / "empty" / "python"))
    monkeypatch.setattr(
        "focus_data_toolkit.official_validator.shutil.which", lambda _: str(exe)
    )
    rc = run_official_validator(
        tmp_path / "data.csv", "1.2.0.1", extra_args=("--output-type", "console")
    )
    assert rc == exit_code
    argv = record.read_text()
    assert "--data-file" in argv
    assert "data.csv" in argv
    assert "--validate-version" in argv
    assert "1.2.0.1" in argv
    assert "--output-type" in argv
