"""FOCUS 1.4 linter tests: numeric format (scientific notation), the x_ custom-key
rule across JSON columns (incl. the Tags exception), and validation levels.

Uses hand-authored rows and official fixtures (tests/fixtures/official/), independent
of the toolkit's generators.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from focus_data_toolkit.model.validator import (
    LEVEL_SEMANTIC,
    LEVEL_STRUCTURAL,
    lint_focus_1_4_structure,
    validate_focus_1_4,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "official"


def _rules_for(dataset, row, column):
    report = lint_focus_1_4_structure(dataset, [row])
    return {v.rule for v in report.violations if v.column == column}


# --------------------------------------------------------------------------- #
# Numeric format — scientific notation (NumericFormat attribute)
# --------------------------------------------------------------------------- #
def test_numeric_format_scientific_notation():
    examples = json.loads((_FIXTURES / "numeric_format_examples.json").read_text())
    for value in examples["valid"]:
        assert "bad_numeric_format" not in _rules_for("Cost and Usage", {"BilledCost": value}, "BilledCost"), value
    for value in examples["invalid"]:
        assert "bad_numeric_format" in _rules_for("Cost and Usage", {"BilledCost": value}, "BilledCost"), value


# --------------------------------------------------------------------------- #
# x_ custom-key rule across JSON columns
# --------------------------------------------------------------------------- #
def test_invoice_detail_grain_focus_and_custom_keys_ok():
    grain = (_FIXTURES / "invoice_detail_grain_example.json").read_text()
    assert "custom_key_not_prefixed" not in _rules_for(
        "Invoice Detail", {"InvoiceDetailGrain": grain}, "InvoiceDetailGrain"
    )


def test_invoice_detail_grain_rejects_non_prefixed_custom_key():
    grain = json.dumps({"GroupedBy": "InvoiceId,ChargeCategory"})
    assert "custom_key_not_prefixed" in _rules_for(
        "Invoice Detail", {"InvoiceDetailGrain": grain}, "InvoiceDetailGrain"
    )


def test_sku_price_details_storageclass_redundancy_are_focus_keys():
    # Redundancy / StorageClass are FOCUS-defined (13-key set) -> must NOT need x_.
    ok = json.dumps({"StorageClass": "Hot", "Redundancy": "Local", "CoreCount": 4})
    assert "custom_key_not_prefixed" not in _rules_for(
        "Cost and Usage", {"SkuPriceDetails": ok}, "SkuPriceDetails"
    )
    bad = json.dumps({"Foo": "bar"})
    assert "custom_key_not_prefixed" in _rules_for(
        "Cost and Usage", {"SkuPriceDetails": bad}, "SkuPriceDetails"
    )


def test_tags_keys_are_not_forced_to_x_prefix():
    # Tags is a Key-Value column whose user keys are arbitrary (no x_ required).
    tags = json.dumps({"Environment": "prod", "CostCenter": "cc-1"})
    assert "custom_key_not_prefixed" not in _rules_for("Cost and Usage", {"Tags": tags}, "Tags")


def test_allocated_method_details_elements_keys():
    ok = json.dumps({"Elements": [{"AllocatedRatio": 0.5, "UsageUnit": "CPU", "UsageQuantity": 0.5, "x_S": "p"}]})
    assert "custom_key_not_prefixed" not in _rules_for(
        "Cost and Usage", {"AllocatedMethodDetails": ok}, "AllocatedMethodDetails"
    )
    bad = json.dumps({"Elements": [{"AllocatedRatio": 0.5, "Bogus": "y"}]})
    assert "custom_key_not_prefixed" in _rules_for(
        "Cost and Usage", {"AllocatedMethodDetails": bad}, "AllocatedMethodDetails"
    )


def test_contract_applied_structure_validated():
    good = (
        '{"Elements":[{"ContractId":"c","ContractCommitmentId":"x",'
        '"ContractCommitmentAppliedCost":1}]}'
    )
    assert "invalid_contract_applied" not in _rules_for(
        "Cost and Usage", {"ContractApplied": good}, "ContractApplied"
    )
    bad = '{"Elements":[{"ContractCommitmentId":"x"}]}'  # missing ContractId + metric
    assert "invalid_contract_applied" in _rules_for(
        "Cost and Usage", {"ContractApplied": bad}, "ContractApplied"
    )


# --------------------------------------------------------------------------- #
# Validation levels — structural well-formedness != full FOCUS conformance
# --------------------------------------------------------------------------- #
_VALID_BILLING_PERIOD = {
    "BillingPeriodStart": "2026-05-01T00:00:00Z",
    "BillingPeriodEnd": "2026-06-01T00:00:00Z",
    "BillingPeriodCreated": "2026-05-01T00:00:00Z",
    "BillingPeriodLastUpdated": "2026-06-01T00:00:00Z",
    "BillingPeriodStatus": "Closed",
    "InvoiceIssuerName": "ExampleCloud",
}


def test_levels_passed_but_never_officially_valid():
    report = lint_focus_1_4_structure("Billing Period", [_VALID_BILLING_PERIOD])
    assert report.ok
    assert set(report.levels_passed) == {LEVEL_STRUCTURAL, LEVEL_SEMANTIC}
    # The linter never asserts cross-dataset or official conformance.
    assert not report.passed("CROSS_DATASET_VALID")
    assert not report.passed("OFFICIALLY_VALIDATED")


def test_semantic_violation_keeps_structural_valid():
    row = dict(_VALID_BILLING_PERIOD, BillingPeriodLastUpdated="2026-04-01T00:00:00Z")
    report = lint_focus_1_4_structure("Billing Period", [row])
    assert report.passed(LEVEL_STRUCTURAL)  # dates are well-formed
    assert not report.passed(LEVEL_SEMANTIC)  # LastUpdated < Created
    assert report.levels_passed == (LEVEL_STRUCTURAL,)


def test_structural_violation_flagged():
    row = dict(_VALID_BILLING_PERIOD, BillingPeriodStatus="Bogus")
    report = lint_focus_1_4_structure("Billing Period", [row])
    assert not report.passed(LEVEL_STRUCTURAL)


def test_validate_focus_1_4_is_deprecated_alias():
    with pytest.warns(DeprecationWarning):
        report = validate_focus_1_4("Billing Period", [_VALID_BILLING_PERIOD])
    assert report.ok
