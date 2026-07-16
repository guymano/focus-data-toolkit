"""Synthetic-mode invariants: assumptions are generated, but always labelled."""

from __future__ import annotations

import pytest

from focus_data_toolkit.convert import convert_to_focus_1_4
from focus_data_toolkit.modes import Mode


@pytest.fixture()
def synthetic_result(source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    return convert_to_focus_1_4(cau, cc, mode=Mode.SYNTHETIC)


def test_all_four_datasets_produced(synthetic_result):
    assert set(synthetic_result.coverage) == {
        "Cost and Usage", "Contract Commitment", "Billing Period", "Invoice Detail"
    }


def test_synthetic_datasets_are_flagged_and_never_fully_conformant(synthetic_result):
    m = synthetic_result.manifest
    for name in ("Billing Period", "Invoice Detail", "Contract Commitment"):
        assert m["datasets"][name]["status"] == "PRODUCED_SYNTHETIC"
        assert m["datasets"][name]["conformance"] == "SYNTHETIC"
    assert m["assumptions_present"] is True


def test_cost_and_usage_stays_a_clean_conversion(synthetic_result):
    entry = synthetic_result.manifest["datasets"]["Cost and Usage"]
    assert entry["status"] == "PRODUCED"
    assert entry["conformance"] == "STRUCTURAL_LINT"


def test_assumed_values_are_marked_assumed(synthetic_result):
    id_cols = synthetic_result.manifest["datasets"]["Invoice Detail"]["columns"]
    for col in ("InvoiceIssueStatus", "PaymentTerms", "ReferenceInvoiceId", "InvoiceDetailId"):
        assert id_cols[col]["lineage"] == "ASSUMED", col


def test_assumptions_are_deterministic(source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    a = convert_to_focus_1_4(cau, cc, mode=Mode.SYNTHETIC, validate=False)
    b = convert_to_focus_1_4(cau, cc, mode=Mode.SYNTHETIC, validate=False)
    assert a.datasets == b.datasets


def test_produced_synthetic_still_passes_structural_lint(synthetic_result):
    # Synthetic data is structurally well-formed even though it is not conformant.
    for report in synthetic_result.reports.values():
        assert report.ok


@pytest.mark.parametrize("mode", ["strict", "synthetic"])
def test_mode_accepts_string(source_tables, mode):
    cau, cc = source_tables[("aws", "1.3")]
    result = convert_to_focus_1_4(cau, cc, mode=mode)
    assert result.mode == Mode(mode)
