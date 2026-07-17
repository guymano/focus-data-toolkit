from __future__ import annotations

import csv
import json

from focus_data_toolkit.cli import main


def _generate(tmp_path, provider, version, rows, seed):
    out_gen = tmp_path / "src"
    assert main(
        [
            "generate", "--provider", provider, "--focus-version", version,
            "--rows", str(rows), "--seed", str(seed), "--out", str(out_gen),
        ]
    ) == 0
    return out_gen


def test_convert_strict_produces_only_cost_and_usage(tmp_path, capsys):
    src = _generate(tmp_path, "aws", "1.3", 60, 1302)
    cau = src / "focus_1_3_cost_and_usage_aws.csv"
    cc = src / "focus_1_3_contract_commitment_aws.csv"
    out14 = tmp_path / "focus-1.4"

    # Strict is the default; result is "incomplete" -> exit code 3.
    rc = main(["convert", "--cost-and-usage", str(cau), "--contract-commitment", str(cc), "--out", str(out14)])
    assert rc == 3

    produced = sorted(p.name for p in out14.glob("*.csv"))
    assert produced == ["focus_1_4_cost_and_usage.csv"]
    assert (out14 / "focus_1_4_manifest.json").exists()
    with open(out14 / "focus_1_4_cost_and_usage.csv", newline="") as fh:
        assert len(next(csv.reader(fh))) == 65

    out = capsys.readouterr().out
    assert "mode: strict" in out
    assert "not produced [Invoice Detail]" in out


def test_convert_synthetic_produces_all_with_synthetic_prefix(tmp_path, capsys):
    src = _generate(tmp_path, "aws", "1.3", 60, 1302)
    cau = src / "focus_1_3_cost_and_usage_aws.csv"
    cc = src / "focus_1_3_contract_commitment_aws.csv"
    out14 = tmp_path / "focus-1.4"

    rc = main(
        [
            "convert", "--cost-and-usage", str(cau), "--contract-commitment", str(cc),
            "--out", str(out14), "--mode", "synthetic",
        ]
    )
    assert rc == 4  # synthetic result with assumptions

    # All four synthetic-mode files carry the synthetic_ prefix (Cost and Usage too,
    # because its InvoiceDetailId back-link into the synthetic Invoice Detail is assumed).
    produced = sorted(p.name for p in out14.glob("*.csv"))
    assert produced == [
        "synthetic_focus_1_4_billing_period.csv",
        "synthetic_focus_1_4_contract_commitment.csv",
        "synthetic_focus_1_4_cost_and_usage.csv",
        "synthetic_focus_1_4_invoice_detail.csv",
    ]
    manifest = json.loads((out14 / "focus_1_4_manifest.json").read_text())
    assert manifest["mode"] == "synthetic"
    assert manifest["assumptions_present"] is True
    assert manifest["datasets"]["Cost and Usage"]["status"] == "PRODUCED_SYNTHETIC"
    assert manifest["datasets"]["Invoice Detail"]["status"] == "PRODUCED_SYNTHETIC"

    err = capsys.readouterr().err
    assert "synthetic mode" in err.lower()


def test_convert_manifest_written_to_explicit_path(tmp_path):
    src = _generate(tmp_path, "aws", "1.3", 40, 1302)
    cau = src / "focus_1_3_cost_and_usage_aws.csv"
    manifest_path = tmp_path / "m.json"
    main(["convert", "--cost-and-usage", str(cau), "--out", str(tmp_path / "v14"), "--manifest", str(manifest_path)])
    assert json.loads(manifest_path.read_text())["target_version"] == "1.4"


def test_convert_from_1_2_strict_reports_not_produced(tmp_path, capsys):
    src = _generate(tmp_path, "gcp", "1.2", 40, 1202)
    cau = src / "focus_1_2_cost_and_usage_gcp.csv"
    rc = main(["convert", "--cost-and-usage", str(cau), "--out", str(tmp_path / "v14")])
    assert rc == 3
    out = capsys.readouterr().out
    assert "source detected: FOCUS 1.2" in out
    assert "not produced [Contract Commitment]" in out


def test_convert_invalid_source_version_exits_2(tmp_path, capsys):
    src = _generate(tmp_path, "aws", "1.3", 20, 1302)
    cau = src / "focus_1_3_cost_and_usage_aws.csv"
    rc = main(
        ["convert", "--cost-and-usage", str(cau), "--out", str(tmp_path / "v14"),
         "--source-version", "foo"]
    )
    assert rc == 2
    assert "error" in capsys.readouterr().err.lower()


def test_convert_forced_wrong_dataset_exits_2(tmp_path, capsys):
    src = _generate(tmp_path, "aws", "1.3", 20, 1302)
    cau = src / "focus_1_3_cost_and_usage_aws.csv"
    # Forcing a dataset the header clearly is not must be rejected (exit 2), not converted.
    rc = main(
        ["convert", "--cost-and-usage", str(cau), "--out", str(tmp_path / "v14"),
         "--source-dataset", "invoice-detail"]
    )
    assert rc == 2


def test_convert_contract_commitment_as_cau_exits_2(tmp_path):
    # A Contract Commitment CSV passed as --cost-and-usage must be rejected even when the
    # version is forced (it is not a Cost and Usage source).
    src = _generate(tmp_path, "aws", "1.3", 20, 1302)
    cc = src / "focus_1_3_contract_commitment_aws.csv"
    rc = main(
        ["convert", "--cost-and-usage", str(cc), "--out", str(tmp_path / "v14"),
         "--source-version", "1.3"]
    )
    assert rc == 2


def test_validate_reports_violations(tmp_path, capsys):
    bad = tmp_path / "bad.csv"
    bad.write_text("BillingPeriodStart,BillingPeriodEnd\n2026-05-01T00:00:00Z,not-a-date\n")
    assert main(["validate", str(bad), "--dataset", "billing-period"]) == 1
    assert "violation" in capsys.readouterr().out


def test_convert_parquet_output(tmp_path):
    import pytest

    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    src = _generate(tmp_path, "aws", "1.3", 40, 1302)
    cau = src / "focus_1_3_cost_and_usage_aws.csv"
    out14 = tmp_path / "pq"

    # Parquet output routes through the streaming engine; synthetic -> exit 4.
    rc = main(["convert", "--cost-and-usage", str(cau), "--out", str(out14),
               "--mode", "synthetic", "--output-format", "parquet"])
    assert rc == 4
    produced = sorted(p.name for p in out14.glob("*.parquet"))
    assert "synthetic_focus_1_4_cost_and_usage.parquet" in produced
    assert not list(out14.glob("*.sqlite"))  # scratch DB never published
    # Decimal columns are stored as decimal128, never float.
    field = pq.ParquetFile(str(out14 / "synthetic_focus_1_4_cost_and_usage.parquet")).schema_arrow.field("BilledCost")
    assert "decimal" in str(field.type)


def test_convert_stream_csv_matches_eager(tmp_path):
    src = _generate(tmp_path, "aws", "1.3", 50, 1302)
    cau = src / "focus_1_3_cost_and_usage_aws.csv"

    eager = tmp_path / "eager"
    assert main(["convert", "--cost-and-usage", str(cau), "--out", str(eager), "--mode", "synthetic"]) == 4
    streamed = tmp_path / "streamed"
    assert main(["convert", "--cost-and-usage", str(cau), "--out", str(streamed),
                 "--mode", "synthetic", "--stream"]) == 4

    for name in sorted(p.name for p in eager.glob("*.csv")):
        assert (eager / name).read_bytes() == (streamed / name).read_bytes(), name


def test_convert_stream_malformed_csv_exits_2(tmp_path, capsys):
    src = _generate(tmp_path, "aws", "1.3", 40, 1302)
    cau = src / "focus_1_3_cost_and_usage_aws.csv"
    lines = cau.read_text().splitlines()
    lines[20] = lines[20] + ",EXTRA_FIELD"  # wrong field count mid-stream
    bad = tmp_path / "bad.csv"
    bad.write_text("\n".join(lines) + "\n")
    rc = main(["convert", "--cost-and-usage", str(bad), "--out", str(tmp_path / "o"),
               "--mode", "synthetic", "--stream"])
    assert rc == 2  # clean error, not a traceback
    assert not (tmp_path / "o").exists()
    assert "error:" in capsys.readouterr().err


def test_convert_parquet_without_pyarrow_exits_2(tmp_path, monkeypatch, capsys):
    import focus_data_toolkit.io.parquet_io as pqio
    from focus_data_toolkit.io.records import MalformedRecordError

    def _no_pyarrow():
        raise MalformedRecordError(pqio._PARQUET_HINT)

    monkeypatch.setattr(pqio, "_require_pyarrow", _no_pyarrow)
    src = _generate(tmp_path, "aws", "1.3", 20, 1302)
    cau = src / "focus_1_3_cost_and_usage_aws.csv"
    rc = main(["convert", "--cost-and-usage", str(cau), "--out", str(tmp_path / "o"),
               "--mode", "synthetic", "--output-format", "parquet"])
    assert rc == 2  # missing optional dependency -> clean error, not a traceback
    assert "PyArrow" in capsys.readouterr().err


def test_convert_stream_honors_manifest_option(tmp_path):
    src = _generate(tmp_path, "aws", "1.3", 30, 1302)
    cau = src / "focus_1_3_cost_and_usage_aws.csv"
    out = tmp_path / "o"
    manifest_copy = tmp_path / "copy_manifest.json"
    rc = main(["convert", "--cost-and-usage", str(cau), "--out", str(out),
               "--mode", "synthetic", "--stream", "--manifest", str(manifest_copy)])
    assert rc == 4
    assert manifest_copy.read_bytes() == (out / "focus_1_4_manifest.json").read_bytes()
