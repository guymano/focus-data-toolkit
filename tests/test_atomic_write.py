"""Atomic writes (P1.7)."""

from __future__ import annotations

import hashlib

import pytest

from focus_data_toolkit.convert import ConversionResult, write_result
from focus_data_toolkit.io.atomic_writer import (
    AtomicOutputDir,
    AtomicWriteError,
    DestinationExistsError,
    OnExists,
    write_files_atomically,
)
from focus_data_toolkit.model.validator import lint_focus_1_4_structure
from focus_data_toolkit.modes import Mode


def _temp_dirs(parent):
    return list(parent.glob(".output.tmp-*"))


# -- AtomicOutputDir / write_files_atomically ---------------------------------

def test_success_publishes_and_cleans_temp(tmp_path):
    dest = tmp_path / "out"
    target = write_files_atomically(
        dest, [("a.csv", b"hello")], final_files={"m.json": b"{}"}
    )
    assert target == dest
    assert (dest / "a.csv").read_bytes() == b"hello"
    assert (dest / "m.json").read_bytes() == b"{}"
    assert not _temp_dirs(tmp_path)


def test_refuse_when_destination_exists(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(DestinationExistsError):
        write_files_atomically(dest, [("a.csv", b"x")])
    # Existing directory untouched, no temp left behind.
    assert list(dest.iterdir()) == []
    assert not _temp_dirs(tmp_path)


def test_replace_swaps_existing_directory(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "old.txt").write_text("old")
    write_files_atomically(dest, [("new.csv", b"new")], on_exists=OnExists.REPLACE)
    assert (dest / "new.csv").exists()
    assert not (dest / "old.txt").exists()  # old content fully replaced
    assert not _temp_dirs(tmp_path)
    assert not list(tmp_path.glob(".trash-*"))


def test_version_preserves_prior_results(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "old.txt").write_text("old")
    target = write_files_atomically(dest, [("new.csv", b"new")], on_exists=OnExists.VERSION)
    assert target.parent == dest
    assert (target / "new.csv").exists()
    assert (dest / "old.txt").exists()  # prior results never touched


def test_interruption_before_commit_leaves_destination_untouched(tmp_path):
    dest = tmp_path / "out"
    with pytest.raises(RuntimeError):
        with AtomicOutputDir(dest) as out:
            out.write_bytes("a.csv", b"partial")
            raise RuntimeError("boom")
    assert not dest.exists()
    assert not _temp_dirs(tmp_path)


def test_keep_temp_retains_staging_on_error(tmp_path):
    dest = tmp_path / "out"
    with pytest.raises(RuntimeError):
        with AtomicOutputDir(dest, keep_temp=True) as out:
            out.write_bytes("a.csv", b"partial")
            raise RuntimeError("boom")
    assert not dest.exists()
    assert _temp_dirs(tmp_path)  # kept for diagnosis


def test_validation_failure_aborts_publish(tmp_path):
    dest = tmp_path / "out"

    def failing_validate():
        raise AtomicWriteError("mandatory validation failed")

    with pytest.raises(AtomicWriteError):
        write_files_atomically(dest, [("a.csv", b"x")], validate=failing_validate)
    assert not dest.exists()
    assert not _temp_dirs(tmp_path)


# -- write_result integration -------------------------------------------------

def _minimal_result(mode=Mode.SYNTHETIC) -> ConversionResult:
    manifest = {
        "tool_version": "0.3.0",
        "source_version": "1.3",
        "target_version": "1.4",
        "mode": mode.value,
        "assumptions_present": False,
        "datasets": {
            "Cost and Usage": {
                "status": "PRODUCED",
                "conformance": "NOT_VALIDATED",
                "columns": {},
                "output_file": "focus_1_4_cost_and_usage.csv",
            }
        },
    }
    return ConversionResult(
        source_version="1.3",
        mode=mode,
        datasets={"Cost and Usage": [{"BilledCost": "1.00", "ChargeCategory": "Usage"}]},
        provenance={},
        manifest=manifest,
    )


def test_write_result_emits_manifest_sidecar_and_checksums(tmp_path):
    dest = tmp_path / "out"
    write_result(_minimal_result(), dest)
    assert (dest / "focus_1_4_cost_and_usage.csv").exists()
    assert (dest / "focus_1_4_manifest.json").exists()
    assert (dest / "_run.json").exists()
    sums_file = dest / "SHA256SUMS"
    assert sums_file.exists()

    sums = {}
    for line in sums_file.read_text().splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    data = (dest / "focus_1_4_cost_and_usage.csv").read_bytes()
    assert sums["focus_1_4_cost_and_usage.csv"] == hashlib.sha256(data).hexdigest()


def test_write_result_refuses_to_publish_lint_failure(tmp_path):
    dest = tmp_path / "out"
    result = _minimal_result()
    # A genuinely failing lint report gates publication.
    result.reports["Cost and Usage"] = lint_focus_1_4_structure(
        "Cost and Usage", [{"BilledCost": "not-a-number"}]
    )
    assert not result.ok
    with pytest.raises(AtomicWriteError):
        write_result(result, dest, require_valid=True)
    assert not dest.exists()


def test_write_result_default_refuses_existing_destination(tmp_path):
    dest = tmp_path / "out"
    write_result(_minimal_result(), dest)
    with pytest.raises(DestinationExistsError):
        write_result(_minimal_result(), dest)


def test_write_result_replace_is_atomic(tmp_path):
    dest = tmp_path / "out"
    write_result(_minimal_result(), dest)
    # A second run with replace succeeds and leaves no temp/trash behind.
    write_result(_minimal_result(), dest, on_exists=OnExists.REPLACE)
    assert (dest / "focus_1_4_cost_and_usage.csv").exists()
    assert not _temp_dirs(tmp_path)
    assert not list(tmp_path.glob(".trash-*"))
