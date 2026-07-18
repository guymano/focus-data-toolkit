"""Runtime configuration: working directory + disk budgets, read once from the environment.

A streaming conversion uses **two filesystems**, budgeted separately:

* the **work** filesystem — scratch state (the SQLite aggregation index and the bundle-validation
  spill), relocatable via ``FOCUS_TOOLKIT_WORK_DIR`` to a faster/larger disk;
* the **output** filesystem — the atomic staging directory and the final files, pinned to the
  parent of ``--out`` by the publish rename (same-``st_dev`` requirement), so it cannot be moved.

Environment variables:

* ``FOCUS_TOOLKIT_WORK_DIR`` — directory for scratch state (default: alongside the output).
* ``FOCUS_TOOLKIT_MAX_WORK_BYTES`` — cap on scratch bytes; exceeding it fails the run (FDT-IO-006).
* ``FOCUS_TOOLKIT_MIN_WORK_FREE_BYTES`` — refuse/abort if the work filesystem free space drops below.
* ``FOCUS_TOOLKIT_MIN_OUTPUT_FREE_BYTES`` — refuse/abort if the output filesystem free space drops below.
* ``FOCUS_TOOLKIT_LOG_LEVEL`` — level for the ``focus_data_toolkit`` logger (default: WARNING).

Sizes accept ``128MB`` / ``512KB`` / ``2GB`` style suffixes or a plain byte count. The pre-flight
estimate is **best-effort with a safety margin** derived from the input size — never a precise
prediction of the space a conversion will consume.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from focus_data_toolkit.errors import Diagnostic, Severity

_InputPaths = Sequence["str | os.PathLike[str] | None"]

_ENV_WORK_DIR = "FOCUS_TOOLKIT_WORK_DIR"
_ENV_MAX_WORK = "FOCUS_TOOLKIT_MAX_WORK_BYTES"
_ENV_MIN_WORK_FREE = "FOCUS_TOOLKIT_MIN_WORK_FREE_BYTES"
_ENV_MIN_OUTPUT_FREE = "FOCUS_TOOLKIT_MIN_OUTPUT_FREE_BYTES"
_ENV_LOG_LEVEL = "FOCUS_TOOLKIT_LOG_LEVEL"

# CSV 1.4 output can be somewhat larger than the source, so the pre-flight uses a margin on the
# input size. Parquet output is smaller, making the estimate a conservative upper bound there.
_OUTPUT_ESTIMATE_MARGIN = 1.3

_SIZE_SUFFIXES = (("KB", 1000), ("MB", 1000**2), ("GB", 1000**3), ("TB", 1000**4), ("B", 1))


class ResourceLimitError(Exception):
    """Raised when a disk budget / free-space check fails (the CLI maps it to exit code 5).

    Carries the structured :class:`~focus_data_toolkit.errors.Diagnostic` (``FDT-IO-005`` for the
    output filesystem, ``FDT-IO-006`` for the work filesystem / temp budget) so callers can
    render it uniformly. It is **not** a ``ConversionError``: raising it inside the atomic output
    context still removes the staging directory (cleanup keys on "not committed", not on the
    exception type), so nothing partial is ever published.
    """

    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


def parse_size(value: str | None) -> int | None:
    """Parse a byte size (``128MB`` / ``512KB`` / ``2GB`` / plain count). ``None``/empty -> ``None``.

    Raises ``ValueError`` on a malformed value.
    """
    if value is None:
        return None
    text = value.strip().upper()
    if not text:
        return None
    for suffix, mult in _SIZE_SUFFIXES:
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * mult)
    return int(text)


def _size_from(env: Mapping[str, str], name: str) -> int | None:
    """Parse a size env var, ignoring (not crashing on) a malformed value."""
    try:
        return parse_size(env.get(name))
    except ValueError:
        return None


@dataclass(frozen=True)
class RuntimeConfig:
    """Resolved runtime configuration (see the module docstring for the env vars)."""

    work_dir: Path | None = None
    max_work_bytes: int | None = None
    min_work_free_bytes: int | None = None
    min_output_free_bytes: int | None = None
    log_level: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RuntimeConfig:
        env = os.environ if env is None else env
        work_dir = env.get(_ENV_WORK_DIR)
        return cls(
            work_dir=Path(work_dir) if work_dir else None,
            max_work_bytes=_size_from(env, _ENV_MAX_WORK),
            min_work_free_bytes=_size_from(env, _ENV_MIN_WORK_FREE),
            min_output_free_bytes=_size_from(env, _ENV_MIN_OUTPUT_FREE),
            log_level=env.get(_ENV_LOG_LEVEL) or None,
        )

    def apply_logging(self) -> None:
        """Configure the ``focus_data_toolkit`` logger from ``FOCUS_TOOLKIT_LOG_LEVEL`` (if set)."""
        if not self.log_level:
            return
        level = logging.getLevelName(self.log_level.strip().upper())
        if isinstance(level, int):
            logging.getLogger("focus_data_toolkit").setLevel(level)


def _free(path: Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _estimate_output_bytes(input_paths: _InputPaths) -> int:
    total = 0
    for raw in input_paths:
        if not raw:
            continue
        try:
            total += Path(raw).stat().st_size
        except OSError:
            continue
    return int(total * _OUTPUT_ESTIMATE_MARGIN)


def _io_error(code: str, message: str, path: Path, **context: str) -> ResourceLimitError:
    ctx = {"path": str(path), **context}
    return ResourceLimitError(
        Diagnostic(code=code, severity=Severity.ERROR, message=message, context=ctx)
    )


def resolve_work_path(config: RuntimeConfig, default_path: Path) -> Path:
    """Where a scratch file should live: under ``WORK_DIR`` (created) if set, else ``default_path``.

    ``default_path`` is the caller's in-staging location; when ``WORK_DIR`` is set the scratch is
    relocated there (off the output filesystem), and the caller becomes responsible for unlinking
    it (the atomic output context only cleans its own staging directory).
    """
    if config.work_dir is None:
        return default_path
    config.work_dir.mkdir(parents=True, exist_ok=True)
    return config.work_dir / Path(default_path).name


def preflight(config: RuntimeConfig, out_parent: Path, input_paths: _InputPaths) -> None:
    """Best-effort pre-flight run *before* any staging; raises on a clear shortfall.

    Estimates the output need from the input size (with a margin) and checks both the output and
    work filesystems against their free space and configured minimums. The estimate is deliberately
    rough — it guards against the obvious "nowhere near enough disk" case, not against every run.
    """
    out_parent = Path(out_parent)
    free_out = _free(out_parent)
    if free_out is not None:
        need = _estimate_output_bytes(input_paths)
        if config.min_output_free_bytes is not None:
            need = max(need, config.min_output_free_bytes)
        if need and free_out < need:
            raise _io_error(
                "FDT-IO-005",
                f"insufficient free space on the output filesystem at {out_parent}: "
                f"need ~{need} bytes, {free_out} free",
                out_parent,
                needed=str(need),
                free=str(free_out),
            )
    work_dir = config.work_dir or out_parent
    free_work = _free(Path(work_dir))
    if (
        free_work is not None
        and config.min_work_free_bytes is not None
        and free_work < config.min_work_free_bytes
    ):
        raise _io_error(
            "FDT-IO-006",
            f"insufficient free space on the work filesystem at {work_dir}: "
            f"need {config.min_work_free_bytes} bytes, {free_work} free",
            Path(work_dir),
            needed=str(config.min_work_free_bytes),
            free=str(free_work),
        )


def enforce_limits(
    config: RuntimeConfig, out_parent: Path, work_dir: Path, scratch_bytes: int
) -> None:
    """In-run enforcement (called periodically): min free space on both filesystems + work budget."""
    if config.min_output_free_bytes is not None:
        free_out = _free(Path(out_parent))
        if free_out is not None and free_out < config.min_output_free_bytes:
            raise _io_error(
                "FDT-IO-005",
                f"output filesystem free space fell below the configured minimum at {out_parent}: "
                f"{free_out} < {config.min_output_free_bytes} bytes",
                Path(out_parent),
                free=str(free_out),
                minimum=str(config.min_output_free_bytes),
            )
    if config.min_work_free_bytes is not None:
        free_work = _free(Path(work_dir))
        if free_work is not None and free_work < config.min_work_free_bytes:
            raise _io_error(
                "FDT-IO-006",
                f"work filesystem free space fell below the configured minimum at {work_dir}: "
                f"{free_work} < {config.min_work_free_bytes} bytes",
                Path(work_dir),
                free=str(free_work),
                minimum=str(config.min_work_free_bytes),
            )
    if config.max_work_bytes is not None and scratch_bytes > config.max_work_bytes:
        raise _io_error(
            "FDT-IO-006",
            f"temporary work budget exceeded: {scratch_bytes} > {config.max_work_bytes} bytes "
            "(FOCUS_TOOLKIT_MAX_WORK_BYTES)",
            Path(work_dir),
            used=str(scratch_bytes),
            budget=str(config.max_work_bytes),
        )


__all__ = [
    "ResourceLimitError",
    "RuntimeConfig",
    "enforce_limits",
    "parse_size",
    "preflight",
    "resolve_work_path",
]
