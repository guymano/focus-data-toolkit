"""Strict-mode invariants: never invent provider-issued financial facts."""

from __future__ import annotations

import pytest

from focus_data_toolkit.convert import convert_to_focus_1_4
from focus_data_toolkit.modes import Mode


@pytest.fixture()
def strict_result(source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    return convert_to_focus_1_4(cau, cc, mode=Mode.STRICT)


def test_billing_period_not_produced_without_authoritative_source(strict_result):
    assert "Billing Period" not in strict_result.coverage
    assert "Billing Period" in strict_result.not_produced


def test_invoice_detail_not_produced_from_cost_and_usage_alone(strict_result):
    assert "Invoice Detail" not in strict_result.coverage


def test_invoice_id_alone_does_not_yield_an_invoice(source_tables):
    # Every source row has an InvoiceId, yet strict produces no Invoice Detail.
    cau, cc = source_tables[("aws", "1.3")]
    assert all(r.get("InvoiceId") for r in cau if r.get("ChargeCategory") != "")
    result = convert_to_focus_1_4(cau, cc, mode=Mode.STRICT)
    assert "Invoice Detail" not in result.datasets


def test_assumed_mandatory_column_blocks_production(strict_result):
    # BillingPeriodStatus is a provider Open/Closed fact -> assumed -> blocks strict.
    blockers = strict_result.manifest["datasets"]["Billing Period"]["blocking_columns"]
    assert "BillingPeriodStatus" in blockers
    assert {"BillingPeriodCreated", "BillingPeriodLastUpdated"} <= set(blockers)


def test_no_contract_terms_invented_in_strict(strict_result):
    assert "Contract Commitment" not in strict_result.datasets
    cc_cols = strict_result.manifest["datasets"]["Contract Commitment"]["columns"]
    # The 1.4-new commercial terms are recorded as assumed, hence never emitted in strict.
    assert cc_cols["ContractCommitmentPaymentModel"]["lineage"] == "ASSUMED"
    assert cc_cols["ContractCommitmentLifecycleStatus"]["lineage"] == "ASSUMED"


def test_created_not_silently_equated_to_period_start(strict_result):
    cc_cols = strict_result.manifest["datasets"]["Contract Commitment"]["columns"]
    assert cc_cols["ContractCommitmentCreated"]["lineage"] == "ASSUMED"


def test_strict_cost_and_usage_lints_clean(strict_result):
    assert strict_result.coverage == ("Cost and Usage",)
    assert strict_result.reports["Cost and Usage"].ok
    assert not strict_result.assumptions_present
