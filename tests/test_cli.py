from __future__ import annotations

import csv

from focus_data_toolkit.cli import main


def test_generate_convert_validate_flow(tmp_path, capsys):
    out_gen = tmp_path / "src"
    assert main(
        [
            "generate", "--provider", "aws", "--focus-version", "1.3",
            "--rows", "60", "--seed", "1302", "--out", str(out_gen),
        ]
    ) == 0
    cau = out_gen / "focus_1_3_cost_and_usage_aws.csv"
    cc = out_gen / "focus_1_3_contract_commitment_aws.csv"
    assert cau.exists() and cc.exists()

    out_14 = tmp_path / "focus-1.4"
    assert main(
        [
            "convert", "--cost-and-usage", str(cau),
            "--contract-commitment", str(cc), "--out", str(out_14),
        ]
    ) == 0
    produced = sorted(p.name for p in out_14.glob("*.csv"))
    assert produced == [
        "focus_1_4_billing_period.csv",
        "focus_1_4_contract_commitment.csv",
        "focus_1_4_cost_and_usage.csv",
        "focus_1_4_invoice_detail.csv",
    ]
    with open(out_14 / "focus_1_4_cost_and_usage.csv", newline="") as fh:
        header = next(csv.reader(fh))
    assert len(header) == 65

    assert main(
        [
            "validate", str(out_14 / "focus_1_4_invoice_detail.csv"),
            "--dataset", "invoice-detail",
        ]
    ) == 0
    assert "OK" in capsys.readouterr().out


def test_convert_from_1_2_reports_partial_coverage(tmp_path, capsys):
    out_gen = tmp_path / "src"
    main(
        [
            "generate", "--provider", "gcp", "--focus-version", "1.2",
            "--rows", "40", "--seed", "1202", "--out", str(out_gen),
        ]
    )
    cau = out_gen / "focus_1_2_cost_and_usage_gcp.csv"
    assert main(["convert", "--cost-and-usage", str(cau), "--out", str(tmp_path / "v14")]) == 0
    out = capsys.readouterr().out
    assert "source detected: FOCUS 1.2" in out
    assert "partial" in out and "Contract Commitment" in out


def test_validate_reports_violations(tmp_path, capsys):
    bad = tmp_path / "bad.csv"
    bad.write_text("BillingPeriodStart,BillingPeriodEnd\n2026-05-01T00:00:00Z,not-a-date\n")
    assert main(["validate", str(bad), "--dataset", "billing-period"]) == 1
    assert "violation" in capsys.readouterr().out
