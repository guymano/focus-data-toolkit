"""Thin wrapper around the official FinOps FOCUS validator.

The official validator (https://github.com/finopsfoundation/focus_validator)
is an optional dependency — install it with::

    pip install "focus-data-toolkit[validator]"

It requires Python >= 3.12 and validates against the FOCUS rule models
published with each FOCUS_Spec release (1.2/1.3 today; 1.4 rule-model support
is expected from the FinOps Foundation later in 2026 — until then the
toolkit's built-in model validator is the 1.4 conformance gate).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


class OfficialValidatorNotInstalled(RuntimeError):
    """Raised when the official focus-validator is not available."""


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
    """Run the official validator on ``data_file``; return its exit code.

    Output streams directly to the console. ``focus_version`` selects the rule
    model (e.g. ``1.2.0.1``); pass-through flags go in ``extra_args``.
    """
    cmd = [
        _executable(),
        "--data-file", str(data_file),
        "--validate-version", focus_version,
        *extra_args,
    ]
    return subprocess.call(cmd)
