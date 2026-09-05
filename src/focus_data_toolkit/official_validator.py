"""Thin wrapper around the official FinOps FOCUS validator.

The official validator (https://github.com/finopsfoundation/focus_validator)
is an optional dependency — install it with::

    pip install "focus-data-toolkit[validator]"

It requires Python >= 3.12 and validates against the FOCUS rule models
published with each FOCUS_Spec release (1.2/1.3 today; 1.4 rule-model support
is expected from the FinOps Foundation later in 2026 — the toolkit's built-in 1.4 validator checks structure and supported cross-dataset invariants,
not complete official conformance).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path


class OfficialValidatorNotInstalled(RuntimeError):
    """Raised when the official focus-validator is not available."""


TESTED_VALIDATOR_VERSION = "2.2.1"


def _console_arguments(args: tuple[str, ...]) -> bool:
    for index, arg in enumerate(args):
        flag, equals, inline = arg.partition("=")
        if flag == "--output-type":
            value = inline if equals else args[index + 1] if index + 1 < len(args) else ""
            if value != "console":
                return False
        elif flag.startswith("--output-"):
            # The wrapper needs the complete console report on stdout, including
            # when argparse would accept an abbreviated output option.
            return False
    return True


def _executable() -> str:
    # Prefer the interpreter's own environment (works even when this CLI was
    # invoked by absolute path and the venv is not on PATH), then fall back
    # to PATH lookup.
    bin_dir = Path(sys.executable).parent
    for name in ("focus-validator", "focus-validator.exe"):
        candidate = bin_dir / name
        if candidate.exists():
            return str(candidate)
    exe = shutil.which("focus-validator")
    if exe is None:
        raise OfficialValidatorNotInstalled(
            "the official FOCUS validator is not installed; run "
            "pip install 'focus-data-toolkit[validator]' (requires Python >= 3.12)"
        )
    return exe


def run_official_validator(
    data_file: str | Path,
    focus_version: str,
    *,
    extra_args: tuple[str, ...] = (),
) -> int:
    """Run the official validator; a complete report with zero FAIL is required for zero.

    Output is captured and printed after completion. The parser is tested against
    2.2.1 console output; other output modes/destinations are rejected (exit 2).
    ``focus_version`` selects the rule model (e.g. ``1.2.0.1``).
    """
    if not _console_arguments(extra_args):
        print("--official requires console output on stdout (tested with focus-validator 2.2.1); "
              "remove conflicting output flags", file=sys.stderr)
        return 2
    cmd = [
        _executable(),
        "--data-file", str(Path(data_file).resolve()),
        "--validate-version", focus_version,
        *extra_args,
    ]
    # The executable can return zero despite rule failures. The public command has
    # no fixture-specific exceptions: only a complete report with no FAIL succeeds.
    from focus_data_toolkit.official_report import parse_report

    cmd += ["--output-type", "console", "--show-violations"]
    try:
        installed = metadata.version("focus-validator")
    except metadata.PackageNotFoundError:
        installed = "unknown (executable found outside this Python environment)"
    if installed != TESTED_VALIDATOR_VERSION:
        print(f"Warning: official console parser tested with focus-validator {TESTED_VALIDATOR_VERSION}; "
              f"installed version is {installed}. Report-format compatibility is not guaranteed.", file=sys.stderr)
    # 2.2.1 resolves its currency resource relative to site-packages. Resolve the
    # input before changing cwd, and force UTF-8 on Windows as well as POSIX.
    spec = importlib.util.find_spec("focus_validator")
    cwd = Path(spec.origin).parent.parent if spec and spec.origin else None
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
    )
    print(proc.stdout, end="")
    print(proc.stderr, end="", file=sys.stderr)
    if proc.returncode:
        return proc.returncode
    if "Traceback (most recent call last)" in proc.stderr:
        return 1
    try:
        report = parse_report(proc.stdout)
    except ValueError as exc:
        print(f"{exc}; expected complete focus-validator 2.2.1 console output", file=sys.stderr)
        return 1
    return 1 if report["failed"] else 0
