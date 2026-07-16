"""Correction scenarios (P1.9): generated correction sets and their integrity checks."""

from __future__ import annotations

from decimal import Decimal

from focus_data_toolkit.generators.scenarios import correction_set
from focus_data_toolkit.validate.bundle import validate_dataset_bundle
from focus_data_toolkit.validate.corrections import (
    check_correction_net_sums,
    check_correction_references,
    check_no_duplicate_charge_keys,
)


def _codes(diags) -> set[str]:
    return {d.code for d in diags}


def test_single_correction_nets_and_references_cleanly():
    rows = correction_set("chg-1", "100.00", ["-30.00"])
    assert check_correction_references(rows) == []
    assert check_correction_net_sums(rows) == []
    assert check_no_duplicate_charge_keys(rows) == []
    # Original stays present and auditable; correction is a distinct keyed row.
    assert rows[0]["x_ChargeKey"] == "chg-1" and rows[0]["ChargeClass"] == ""
    assert rows[1]["ChargeClass"] == "Correction" and rows[1]["x_CorrectionOf"] == "chg-1"


def test_running_net_of_multiple_corrections():
    rows = correction_set("chg-1", "100.00", ["-30.00", "-20.00", "5.00"])
    assert check_correction_net_sums(rows) == []
    # x_NetCharge is the running net after each correction.
    nets = [r["x_NetCharge"] for r in rows if r.get("x_CorrectionOf")]
    assert nets == ["70.00", "50.00", "55.00"]


def test_late_credit_note_reconciles():
    rows = correction_set("chg-9", "1000.00", ["-1000.00"], charge_category="Purchase")
    assert check_correction_net_sums(rows) == []
    assert rows[1]["ChargeCategory"] == "Credit"  # negative correction is a credit
    assert Decimal(rows[1]["x_NetCharge"]) == Decimal("0.00")


def test_wrong_declared_net_is_flagged():
    rows = correction_set("chg-1", "100.00", ["-30.00"])
    rows[1]["x_NetCharge"] = "999.00"  # tampered running net
    assert "FDT-CORR-002" in _codes(check_correction_net_sums(rows))


def test_duplicate_charge_key_is_an_overwrite():
    rows = correction_set("chg-1", "100.00", ["-30.00"])
    rows[1]["x_ChargeKey"] = "chg-1"  # correction reuses the original key -> overwrite
    assert "FDT-CORR-003" in _codes(check_no_duplicate_charge_keys(rows))


def test_correction_referencing_missing_original_is_flagged():
    rows = correction_set("chg-1", "100.00", ["-30.00"])
    del rows[0]  # drop the original -> reference dangles
    assert "FDT-CORR-001" in _codes(check_correction_references(rows))


def test_multi_currency_corrections_are_independent():
    usd = correction_set("usd-1", "100.00", ["-40.00"], invoice_id="INV-U", currency="USD")
    eur = correction_set("eur-1", "80.00", ["-10.00"], invoice_id="INV-E", currency="EUR")
    rows = usd + eur
    assert check_correction_net_sums(rows) == []
    assert check_no_duplicate_charge_keys(rows) == []


def test_corrections_flow_through_the_bundle_validator():
    good = correction_set("chg-1", "100.00", ["-30.00"])
    report = validate_dataset_bundle({"Cost and Usage": good})
    assert report.ok
    assert "correction_net_sums" in report.checks_run
    assert "no_duplicate_charge_keys" in report.checks_run

    bad = correction_set("chg-2", "100.00", ["-30.00"])
    bad[1]["x_NetCharge"] = "0.00"
    report = validate_dataset_bundle({"Cost and Usage": bad})
    assert not report.ok
    assert "FDT-CORR-002" in _codes(report.errors)
