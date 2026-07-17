"""Bundle validation as a publication gate (PR-11): eager + streaming, spill, escape hatch."""

from __future__ import annotations

import json

import pytest
from test_supplement_apply import (
    _billing_period_rows,
    _cc_supplement_rows,
    _invoice_line_rows,
    _invoice_rows,
    write_csv,
)

from focus_data_toolkit.cli import main
from focus_data_toolkit.convert import (
    AtomicWriteError,
    convert_files,
    convert_to_focus_1_4,
    write_result,
)
from focus_data_toolkit.modes import Mode
from focus_data_toolkit.storage.spill import SpillableIndexPool
from focus_data_toolkit.supplement import SupplementBundle, SupplementFileSpec
from focus_data_toolkit.validate.bundle import validate_dataset_bundle


@pytest.fixture
def source(source_tables):
    cau, _ = source_tables[("aws", "1.2")]
    return cau


@pytest.fixture
def cc_source(source_tables):
    _, cc = source_tables[("aws", "1.3")]
    return cc


@pytest.fixture
def full_bundle(tmp_path, source, cc_source) -> SupplementBundle:
    files = {
        "bp.csv": _billing_period_rows(source),
        "invoices.csv": _invoice_rows(source),
        "lines.csv": _invoice_line_rows(source),
        "commitments.csv": _cc_supplement_rows(cc_source),
    }
    return SupplementBundle.load(
        [SupplementFileSpec(path=write_csv(tmp_path / n, rows)) for n, rows in files.items()]
    )


# --- eager path -------------------------------------------------------------------------


def test_eager_publish_records_bundle_validation(tmp_path, source):
    result = convert_to_focus_1_4(source, mode=Mode.SYNTHETIC)
    out = tmp_path / "out"
    write_result(result, out)
    manifest = json.loads((out / "focus_1_4_manifest.json").read_text(encoding="utf-8"))
    section = manifest["bundle_validation"]
    assert section["ok"] is True
    assert "cost_and_usage_invoice_detail_fk" in section["checks_run"]
    assert "billing_period_coverage" in section["checks_run"]
    # convert_to_focus_1_4 recorded the identical section at convert time.
    assert result.manifest["bundle_validation"] == section
    assert result.bundle_report is not None and result.bundle_report.ok


def test_eager_dangling_invoice_detail_id_blocks_publication(tmp_path, source):
    result = convert_to_focus_1_4(source, mode=Mode.SYNTHETIC)
    assert result.datasets["Invoice Detail"], "fixture must produce invoice lines"
    # Corrupt the bundle after conversion: drop one invoice line, leaving Cost and Usage
    # rows back-linking to a now-missing InvoiceDetailId.
    result.datasets["Invoice Detail"].pop(0)
    out = tmp_path / "out"
    with pytest.raises(AtomicWriteError, match="bundle validation failed"):
        write_result(result, out)
    assert not out.exists(), "nothing may be published when the bundle gate fails"
    assert result.bundle_report is not None and not result.bundle_report.ok
    assert any(d.code == "FDT-CROSS-014" for d in result.bundle_report.errors)


def test_eager_escape_hatch_records_skip(tmp_path, source):
    result = convert_to_focus_1_4(source, mode=Mode.SYNTHETIC, validate=False)
    result.datasets["Invoice Detail"].pop(0)  # would fail the gate
    out = tmp_path / "out"
    write_result(result, out, validate_bundle=False)
    manifest = json.loads((out / "focus_1_4_manifest.json").read_text(encoding="utf-8"))
    assert manifest["bundle_validation"] == {"skipped": True}


# --- streaming path ---------------------------------------------------------------------


def _write_source(tmp_path, rows, name="cau.csv"):
    return write_csv(tmp_path / name, rows)


def test_streaming_publish_records_bundle_validation(tmp_path, source):
    out = convert_files(
        _write_source(tmp_path, source), tmp_path / "out", mode=Mode.SYNTHETIC
    )
    manifest = json.loads((out / "focus_1_4_manifest.json").read_text(encoding="utf-8"))
    section = manifest["bundle_validation"]
    assert section["ok"] is True
    assert "cost_and_usage_invoice_detail_fk" in section["checks_run"]
    # The scratch spill DB is never published, and SHA256SUMS covers only real outputs.
    assert not (out / "_bundle_index.sqlite").exists()
    assert "_bundle_index.sqlite" not in (out / "SHA256SUMS").read_text(encoding="utf-8")


def test_streaming_gate_blocks_publication(tmp_path, source, monkeypatch):
    # The honest converter cannot produce a broken bundle, so fail the gate by injection
    # to prove the wiring: an ERROR report must abort before anything is published.
    from focus_data_toolkit.errors import Diagnostic, Severity
    from focus_data_toolkit.validate import bundle as bundle_mod

    def failing(bundle, **kwargs):
        return bundle_mod.BundleReport(
            diagnostics=[
                Diagnostic(
                    code="FDT-CROSS-014", severity=Severity.ERROR, message="injected",
                    datasets=("Cost and Usage",),
                )
            ],
            checks_run=("cost_and_usage_invoice_detail_fk",),
        )

    monkeypatch.setattr(bundle_mod, "validate_dataset_bundle", failing)
    out_dir = tmp_path / "out"
    with pytest.raises(AtomicWriteError, match="bundle validation failed"):
        convert_files(_write_source(tmp_path, source), out_dir, mode=Mode.SYNTHETIC)
    assert not out_dir.exists()


def test_streaming_no_validate_records_skip(tmp_path, source):
    out = convert_files(
        _write_source(tmp_path, source), tmp_path / "out", mode=Mode.SYNTHETIC,
        validate=False,
    )
    manifest = json.loads((out / "focus_1_4_manifest.json").read_text(encoding="utf-8"))
    assert manifest["bundle_validation"] == {"skipped": True}


def test_streaming_strict_supplemented_four_dataset_bundle_passes(
    tmp_path, source, cc_source, full_bundle
):
    out = convert_files(
        _write_source(tmp_path, source), tmp_path / "out",
        contract_commitment=write_csv(tmp_path / "cc.csv", cc_source),
        mode=Mode.STRICT, supplements=full_bundle,
    )
    manifest = json.loads((out / "focus_1_4_manifest.json").read_text(encoding="utf-8"))
    assert all(e["status"] == "PRODUCED" for e in manifest["datasets"].values())
    section = manifest["bundle_validation"]
    assert section["ok"] is True
    # The post-Lot-2 high-value checks all ran against the real client-id back-links.
    assert {
        "unique_invoice_detail_ids",
        "cost_and_usage_invoice_detail_fk",
        "cost_and_usage_invoice_detail_consistency",
        "billing_period_coverage",
        "contract_applied_fk",
    } <= set(section["checks_run"])


def test_cli_no_validate_skips_bundle_gate(tmp_path, source):
    src = _write_source(tmp_path, source)
    out = tmp_path / "cli-out"
    rc = main([
        "convert", "--cost-and-usage", str(src), "--out", str(out),
        "--mode", "synthetic", "--no-validate",
    ])
    assert rc in (0, 4)  # synthetic mode exits 4 when assumptions are present
    manifest = json.loads((out / "focus_1_4_manifest.json").read_text(encoding="utf-8"))
    assert manifest["bundle_validation"] == {"skipped": True}


# --- bounded-memory contract ------------------------------------------------------------


def test_bundle_rejects_one_shot_iterators(source):
    result = convert_to_focus_1_4(source, mode=Mode.SYNTHETIC, validate=False)
    bundle = dict(result.datasets)
    bundle["Cost and Usage"] = iter(bundle["Cost and Usage"])  # one-shot
    with pytest.raises(TypeError, match="one-shot iterator"):
        validate_dataset_bundle(bundle)


def test_spilled_validation_equals_in_memory_validation(tmp_path, source, cc_source, full_bundle):
    result = convert_to_focus_1_4(
        source, cc_source, mode=Mode.STRICT, supplements=full_bundle, validate=False
    )
    reference = validate_dataset_bundle(result.datasets)
    pool = SpillableIndexPool(tmp_path / "spill.sqlite", threshold=1)  # spill everything
    try:
        spilled = validate_dataset_bundle(result.datasets, index_factory=pool.make_map)
    finally:
        pool.close()
    assert pool.spilled, "threshold=1 must actually exercise the SQLite path"
    assert spilled.as_dict() == reference.as_dict()
