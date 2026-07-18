"""Runtime config + separate work/output disk budgets and pre-flight (Lot A / A3)."""

from __future__ import annotations

from pathlib import Path

import pytest

import focus_data_toolkit.runtime as rt
from focus_data_toolkit.convert import convert_files
from focus_data_toolkit.generators import get_generator
from focus_data_toolkit.runtime import (
    ResourceLimitError,
    RuntimeConfig,
    enforce_limits,
    parse_size,
    preflight,
    work_run_dir,
)


def _cau(tmp_path: Path, n: int = 300, seed: int = 7) -> Path:
    src = tmp_path / "cau.csv"
    src.write_bytes(get_generator("aws", "1.3").generate_csv_bytes(n, seed))
    return src


def test_parse_size():
    assert parse_size(None) is None
    assert parse_size("") is None
    assert parse_size("1024") == 1024
    assert parse_size("512KB") == 512_000
    assert parse_size("128MB") == 128_000_000
    assert parse_size("2GB") == 2_000_000_000
    with pytest.raises(ValueError):
        parse_size("not-a-size")


def test_config_from_env_reads_separate_budgets():
    cfg = RuntimeConfig.from_env(
        {
            "FOCUS_TOOLKIT_WORK_DIR": "/scratch",
            "FOCUS_TOOLKIT_MAX_WORK_BYTES": "10MB",
            "FOCUS_TOOLKIT_MIN_WORK_FREE_BYTES": "5MB",
            "FOCUS_TOOLKIT_MIN_OUTPUT_FREE_BYTES": "1GB",
        }
    )
    assert str(cfg.work_dir) == "/scratch"
    assert cfg.max_work_bytes == 10_000_000
    assert cfg.min_work_free_bytes == 5_000_000
    assert cfg.min_output_free_bytes == 1_000_000_000


def test_config_ignores_malformed_budget():
    cfg = RuntimeConfig.from_env({"FOCUS_TOOLKIT_MAX_WORK_BYTES": "banana"})
    assert cfg.max_work_bytes is None


def test_preflight_output_shortfall_raises_io_005(tmp_path):
    cfg = RuntimeConfig(min_output_free_bytes=10**18)
    with pytest.raises(ResourceLimitError) as exc:
        preflight(cfg, tmp_path, [])
    assert exc.value.diagnostic.code == "FDT-IO-005"


def test_preflight_work_shortfall_raises_io_006(tmp_path):
    cfg = RuntimeConfig(min_work_free_bytes=10**18)
    with pytest.raises(ResourceLimitError) as exc:
        preflight(cfg, tmp_path, [])
    assert exc.value.diagnostic.code == "FDT-IO-006"


def test_enforce_work_budget_raises_io_006(tmp_path):
    cfg = RuntimeConfig(max_work_bytes=100)
    with pytest.raises(ResourceLimitError) as exc:
        enforce_limits(cfg, tmp_path, tmp_path, scratch_bytes=200)
    assert exc.value.diagnostic.code == "FDT-IO-006"


def test_enforce_within_budget_is_noop(tmp_path):
    enforce_limits(RuntimeConfig(max_work_bytes=1000), tmp_path, tmp_path, scratch_bytes=10)


def test_work_dir_relocation_is_byte_identical_and_cleans_scratch(tmp_path, monkeypatch):
    src = _cau(tmp_path)
    default_out = tmp_path / "default"
    convert_files(src, default_out, mode="synthetic")

    work = tmp_path / "work"
    monkeypatch.setenv("FOCUS_TOOLKIT_WORK_DIR", str(work))
    reloc_out = tmp_path / "reloc"
    convert_files(src, reloc_out, mode="synthetic")

    # Business artifacts are identical wherever the scratch lives.
    assert (default_out / "SHA256SUMS").read_bytes() == (reloc_out / "SHA256SUMS").read_bytes()
    name = "synthetic_focus_1_4_cost_and_usage.csv"
    assert (default_out / name).read_bytes() == (reloc_out / name).read_bytes()
    # Relocated scratch is cleaned up and never published.
    assert not list(work.glob("*.sqlite"))
    assert not list(reloc_out.glob("*.sqlite"))


def test_preflight_min_output_free_blocks_convert(tmp_path, monkeypatch):
    src = _cau(tmp_path)
    monkeypatch.setenv("FOCUS_TOOLKIT_MIN_OUTPUT_FREE_BYTES", str(10**18))
    with pytest.raises(ResourceLimitError) as exc:
        convert_files(src, tmp_path / "out", mode="synthetic")
    assert exc.value.diagnostic.code == "FDT-IO-005"
    assert not (tmp_path / "out").exists()


# --- PR #23 remediation ------------------------------------------------------------------


def test_work_run_dir_is_per_run(tmp_path):
    cfg = RuntimeConfig(work_dir=tmp_path / "w")
    a = work_run_dir(cfg, "run-A")
    b = work_run_dir(cfg, "run-B")
    assert a is not None and b is not None
    assert a != b  # per-run scoping -> concurrent runs sharing WORK_DIR never collide
    assert a.parent == (tmp_path / "w") and a.is_dir()
    assert work_run_dir(RuntimeConfig(), "x") is None  # no WORK_DIR -> no relocation


def test_preflight_requires_estimate_plus_reserve(tmp_path, monkeypatch):
    # input ~1000 bytes -> estimate 1300; reserve 2000; need 3300 > free 3000 -> must fail now.
    # (The old max(1300, 2000) = 2000 <= 3000 would have wrongly passed.)
    src = tmp_path / "in.csv"
    src.write_bytes(b"x" * 1000)
    monkeypatch.setattr(rt, "_free", lambda _p: 3000)
    cfg = RuntimeConfig(min_output_free_bytes=2000)
    with pytest.raises(ResourceLimitError) as exc:
        preflight(cfg, tmp_path, [str(src)])
    assert exc.value.diagnostic.code == "FDT-IO-005"


def test_max_work_bytes_enforced_default_config_small_input(tmp_path, monkeypatch):
    # No WORK_DIR (scratch stays in staging) and an input shorter than the progress step:
    # the default scratch is still tracked and the post-loop guard enforces the budget.
    src = _cau(tmp_path, n=200)
    monkeypatch.setenv("FOCUS_TOOLKIT_MAX_WORK_BYTES", "1")
    with pytest.raises(ResourceLimitError) as exc:
        convert_files(src, tmp_path / "out", mode="synthetic")
    assert exc.value.diagnostic.code == "FDT-IO-006"
    assert not (tmp_path / "out").exists()
