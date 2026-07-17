"""Streaming supplements: byte-identical to the eager path, CLI surface included."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_supplement_apply import (
    _billing_period_rows,
    _cc_supplement_rows,
    _invoice_line_rows,
    _invoice_rows,
    write_csv,
)

from focus_data_toolkit.cli import main
from focus_data_toolkit.convert import convert_files, convert_to_focus_1_4, rows_to_csv_bytes
from focus_data_toolkit.modes import Mode
from focus_data_toolkit.supplement import SupplementBundle, SupplementFileSpec


@pytest.fixture
def source(source_tables):
    cau, _ = source_tables[("aws", "1.2")]
    return cau


@pytest.fixture
def cc_source(source_tables):
    _, cc = source_tables[("aws", "1.3")]
    return cc


@pytest.fixture
def supplement_paths(tmp_path, source, cc_source) -> dict[str, Path]:
    return {
        name: write_csv(tmp_path / name, rows)
        for name, rows in {
            "bp.csv": _billing_period_rows(source),
            "invoices.csv": _invoice_rows(source),
            "lines.csv": _invoice_line_rows(source),
            "commitments.csv": _cc_supplement_rows(cc_source),
        }.items()
    }


@pytest.fixture
def source_files(tmp_path, source, cc_source) -> tuple[Path, Path]:
    return (
        write_csv(tmp_path / "cau.csv", source),
        write_csv(tmp_path / "cc.csv", cc_source),
    )


@pytest.mark.parametrize("mode", [Mode.STRICT, Mode.SYNTHETIC])
def test_streaming_equals_eager_with_supplements(
    tmp_path, source, cc_source, supplement_paths, source_files, mode
):
    bundle = SupplementBundle.load(
        [SupplementFileSpec(path=p) for p in supplement_paths.values()]
    )
    eager = convert_to_focus_1_4(source, cc_source, mode=mode, supplements=bundle)
    cau_file, cc_file = source_files
    out = convert_files(
        cau_file, tmp_path / f"stream-{mode}", contract_commitment=cc_file,
        mode=mode, supplements=bundle,
    )
    # Every produced dataset byte-identical to the eager reference.
    assert set(eager.datasets) == {
        n for n, e in eager.manifest["datasets"].items() if "output_file" in e
    }
    for name, rows in eager.datasets.items():
        fname = eager.manifest["datasets"][name]["output_file"]
        expected = rows_to_csv_bytes(rows)
        assert (out / fname).read_bytes() == expected, (mode, name)
    # Manifests identical except operational extras (none here): compare rendered JSON.
    streamed_manifest = json.loads((out / "focus_1_4_manifest.json").read_text())
    assert streamed_manifest == eager.manifest


def test_streaming_strict_full_supplements_produces_four_files(
    tmp_path, source_files, supplement_paths
):
    cau_file, cc_file = source_files
    bundle = SupplementBundle.load(
        [SupplementFileSpec(path=p) for p in supplement_paths.values()]
    )
    out = convert_files(
        cau_file, tmp_path / "strict-out", contract_commitment=cc_file,
        mode=Mode.STRICT, supplements=bundle,
    )
    manifest = json.loads((out / "focus_1_4_manifest.json").read_text())
    produced = {n for n, e in manifest["datasets"].items() if e["status"] == "PRODUCED"}
    assert produced == {"Cost and Usage", "Billing Period", "Invoice Detail",
                        "Contract Commitment"}
    assert manifest["assumptions_present"] is False
    assert {e["kind"] for e in manifest["supplements"]} == {
        "billing_period", "invoice", "invoice_line", "contract_commitment"
    }


def test_streaming_supplement_error_fails_before_staging(tmp_path, source_files):
    cau_file, _ = source_files
    rows = [
        {"InvoiceIssuerName": "AWS", "BillingPeriodStart": "2026-05-01T00:00:00Z",
         "BillingPeriodEnd": "2026-06-01T00:00:00Z", "BillingPeriodStatus": "Done"},
    ]
    bad = write_csv(tmp_path / "bad.csv", rows)
    bundle = SupplementBundle.load([SupplementFileSpec(path=bad)])
    from focus_data_toolkit.convert import ConversionError

    out_dir = tmp_path / "never"
    with pytest.raises(ConversionError, match="FDT-SUPP-004"):
        convert_files(cau_file, out_dir, mode=Mode.STRICT, supplements=bundle)
    assert not out_dir.exists()


def test_cli_convert_with_supplements_strict(tmp_path, source_files, supplement_paths):
    cau_file, cc_file = source_files
    out_dir = tmp_path / "cli-out"
    argv = ["convert", "--cost-and-usage", str(cau_file),
            "--contract-commitment", str(cc_file), "--out", str(out_dir),
            "--mode", "strict"]
    for p in supplement_paths.values():
        argv += ["--supplement", str(p)]
    rc = main(argv)
    assert rc == 0  # strict AND complete: nothing NOT_PRODUCED, no synthetic warning
    manifest = json.loads((out_dir / "focus_1_4_manifest.json").read_text())
    assert manifest["assumptions_present"] is False


def test_cli_convert_stream_with_supplements_dir(tmp_path, source_files, supplement_paths):
    cau_file, cc_file = source_files
    supp_dir = tmp_path / "supp"
    supp_dir.mkdir()
    entries = []
    for p in supplement_paths.values():
        (supp_dir / p.name).write_bytes(p.read_bytes())
        entries.append({"path": p.name, "provenance": "billing portal export"})
    (supp_dir / "supplements.json").write_text(
        json.dumps({"supplement_format": "1", "files": entries}), encoding="utf-8"
    )
    out_dir = tmp_path / "cli-stream-out"
    rc = main(["convert", "--cost-and-usage", str(cau_file),
               "--contract-commitment", str(cc_file), "--out", str(out_dir),
               "--mode", "strict", "--stream", "--supplements-dir", str(supp_dir)])
    assert rc == 0
    manifest = json.loads((out_dir / "focus_1_4_manifest.json").read_text())
    assert all(e["status"] == "PRODUCED" for e in manifest["datasets"].values())
    assert all(s["provenance"] == "billing portal export" for s in manifest["supplements"])
