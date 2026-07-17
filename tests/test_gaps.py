"""Gap analysis (``fdt gaps``): computed from the real provenance rules, never hardcoded."""

from __future__ import annotations

import json

import pytest

from focus_data_toolkit.cli import main
from focus_data_toolkit.convert.billing_period import PROVENANCE as BP_PROVENANCE
from focus_data_toolkit.model import FOCUS_1_4_DATASETS
from focus_data_toolkit.model.validator import load_model
from focus_data_toolkit.provenance import strict_blockers
from focus_data_toolkit.supplement import SUPPLEMENT_KINDS, compute_gaps

# A partial header (client extract missing mandatory 1.x columns) — used to check the
# source-completeness reporting; realistic full headers come from the generator fixture.
PARTIAL_SOURCE_1_2 = (
    "ProviderName", "PublisherName", "InvoiceIssuerName", "InvoiceId", "BillingAccountId",
    "BillingCurrency", "BillingPeriodStart", "BillingPeriodEnd", "ChargeCategory",
    "BilledCost", "EffectiveCost",
)


@pytest.fixture
def full_1_2_header(source_tables):
    cau, _ = source_tables[("aws", "1.2")]
    return tuple(cau[0].keys())
CC_1_3 = (
    "BillingCurrency", "ContractCommitmentCategory", "ContractCommitmentCost",
    "ContractCommitmentDescription", "ContractCommitmentId", "ContractCommitmentPeriodEnd",
    "ContractCommitmentPeriodStart", "ContractCommitmentQuantity", "ContractCommitmentType",
    "ContractCommitmentUnit", "ContractId", "ContractPeriodEnd", "ContractPeriodStart",
)


def test_billing_period_gaps_match_the_provenance_blockers(full_1_2_header):
    report = compute_gaps(full_1_2_header, "1.2")
    blocking = {g.column for g in report.blocking("Billing Period")}
    model = load_model()
    expected = set(strict_blockers(BP_PROVENANCE, model["datasets"]["Billing Period"]["columns"]))
    assert blocking == expected
    assert blocking == {
        "BillingPeriodCreated", "BillingPeriodLastUpdated", "BillingPeriodStatus"
    }


def test_every_derived_dataset_blocking_gap_names_a_supplement_kind(full_1_2_header):
    report = compute_gaps(full_1_2_header, "1.2", cc_columns=CC_1_3)
    for name in ("Billing Period", "Invoice Detail", "Contract Commitment"):
        assert report.blocking(name), name
        for gap in report.blocking(name):
            assert gap.supplement_kinds, (name, gap.column)
            for kind in gap.supplement_kinds:
                assert gap.column in SUPPLEMENT_KINDS[kind].columns


def test_incomplete_source_reports_cost_and_usage_gaps_without_kind():
    # Mandatory 1.x columns missing from the source itself: honest source-completeness
    # gaps — no supplement kind can fabricate them.
    report = compute_gaps(PARTIAL_SOURCE_1_2, "1.2")
    cu = report.blocking("Cost and Usage")
    assert cu and all(g.supplement_kinds == () for g in cu)
    assert {"ChargePeriodStart", "ChargePeriodEnd"} <= {g.column for g in cu}


def test_complete_source_has_no_cost_and_usage_gaps(full_1_2_header):
    assert compute_gaps(full_1_2_header, "1.2").blocking("Cost and Usage") == ()


def test_invoice_detail_needs_both_invoice_and_line_kinds(full_1_2_header):
    report = compute_gaps(full_1_2_header, "1.2")
    kinds = {k for g in report.blocking("Invoice Detail") for k in g.supplement_kinds}
    assert kinds == {"invoice", "invoice_line"}


def test_missing_cc_source_is_a_dataset_level_gap(full_1_2_header):
    report = compute_gaps(full_1_2_header, "1.2")
    assert "Contract Commitment" in report.dataset_level_gaps
    with_cc = compute_gaps(full_1_2_header, "1.2", cc_columns=CC_1_3)
    assert "Contract Commitment" not in with_cc.dataset_level_gaps
    assert with_cc.blocking("Contract Commitment")


def test_gap_report_json_shape_and_templates(full_1_2_header):
    payload = compute_gaps(full_1_2_header, "1.2").as_dict()
    assert payload["gap_report_format"] == "1"
    assert payload["source_version"] == "1.2"
    assert set(payload["datasets"]) == set(FOCUS_1_4_DATASETS)
    assert payload["datasets"]["Cost and Usage"]["strictly_producible_as_is"] is True
    bp = payload["supplement_templates"]["billing_period"]
    assert bp["csv_header"].startswith("InvoiceIssuerName,BillingPeriodStart,BillingPeriodEnd")
    # Allowed values from the model surface on the gap (e.g. BillingPeriodStatus).
    status_gap = next(
        g for g in payload["datasets"]["Billing Period"]["column_gaps"]
        if g["column"] == "BillingPeriodStatus"
    )
    assert status_gap["allowed_values"]


def test_gaps_cli_json_output(tmp_path, source_tables):
    import csv

    cau, _ = source_tables[("aws", "1.2")]
    src = tmp_path / "cau.csv"
    with open(src, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(cau[0].keys()))
        writer.writeheader()
        writer.writerows(cau)
    out = tmp_path / "gaps.json"
    rc = main(["gaps", "--cost-and-usage", str(src), "--format", "json", "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["source_version"] == "1.2"
    assert payload["datasets"]["Billing Period"]["column_gaps"]
