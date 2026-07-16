"""End-to-end: generate 1.2/1.3 -> convert -> every produced 1.4 dataset conforms."""

from __future__ import annotations

from decimal import Decimal

import pytest

from focus_data_toolkit.convert import convert_to_focus_1_4
from focus_data_toolkit.model import FOCUS_1_4_DATASETS, dataset_columns

MATRIX = [(p, v) for p in ("aws", "azure", "gcp") for v in ("1.2", "1.3")]


@pytest.mark.parametrize(("provider", "version"), MATRIX)
def test_roundtrip_all_datasets_conform(source_tables, provider, version):
    cau, cc = source_tables[(provider, version)]
    result = convert_to_focus_1_4(cau, cc)
    assert result.source_version == version
    for name, report in result.reports.items():
        assert report.ok, f"{provider} {version} {name}: {report.messages()[:10]}"


@pytest.mark.parametrize(("provider", "version"), MATRIX)
def test_coverage_is_honest(source_tables, provider, version):
    cau, cc = source_tables[(provider, version)]
    result = convert_to_focus_1_4(cau, cc)
    if version == "1.3":
        assert set(result.coverage) == set(FOCUS_1_4_DATASETS)
    else:
        # 1.2 has no Contract Commitment dataset: coverage must stay partial,
        # never fabricated.
        assert "Contract Commitment" not in result.coverage
        assert set(result.coverage) == set(FOCUS_1_4_DATASETS) - {"Contract Commitment"}


def test_cau_output_has_exact_1_4_columns(source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    result = convert_to_focus_1_4(cau, cc)
    out = result.datasets["Cost and Usage"]
    assert tuple(out[0].keys()) == dataset_columns("Cost and Usage")
    assert "ProviderName" not in out[0] and "PublisherName" not in out[0]
    assert len(out) == len(cau)


def test_deprecated_source_columns_are_the_only_drops(source_tables):
    cau, _ = source_tables[("aws", "1.3")]
    target = set(dataset_columns("Cost and Usage"))
    dropped = set(cau[0].keys()) - target
    assert dropped == {"ProviderName", "PublisherName"}


def test_invoice_detail_reconciles_with_cost_and_usage(source_tables):
    cau, cc = source_tables[("gcp", "1.3")]
    result = convert_to_focus_1_4(cau, cc)
    details = result.datasets["Invoice Detail"]
    assert details
    for detail in details:
        members = [
            r
            for r in cau
            if r.get("InvoiceId") == detail["InvoiceId"]
            and r.get("ChargeCategory") == detail["ChargeCategory"]
        ]
        expected = sum(Decimal(r["BilledCost"] or "0") for r in members)
        assert Decimal(detail["BilledCost"]) == expected.quantize(Decimal("0.000001"))


def test_cau_rows_backlink_to_invoice_details(source_tables):
    cau, cc = source_tables[("azure", "1.3")]
    result = convert_to_focus_1_4(cau, cc)
    detail_ids = {d["InvoiceDetailId"] for d in result.datasets["Invoice Detail"]}
    for row in result.datasets["Cost and Usage"]:
        if row["InvoiceId"]:
            assert row["InvoiceDetailId"] in detail_ids
        else:
            assert row["InvoiceDetailId"] == ""


def test_billing_periods_cover_all_source_periods(source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    result = convert_to_focus_1_4(cau, cc)
    periods = {
        (r["BillingPeriodStart"], r["BillingPeriodEnd"]) for r in cau
    }
    derived = {
        (r["BillingPeriodStart"], r["BillingPeriodEnd"])
        for r in result.datasets["Billing Period"]
    }
    assert derived == periods


def test_contract_commitment_expands_to_30_columns(source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    result = convert_to_focus_1_4(cau, cc)
    out = result.datasets["Contract Commitment"]
    assert len(out) == len(cc)
    assert tuple(out[0].keys()) == dataset_columns("Contract Commitment")
    assert len(out[0]) == 30
    # Source values are preserved verbatim.
    assert out[0]["ContractCommitmentId"] == cc[0]["ContractCommitmentId"]
    assert out[0]["ContractCommitmentCost"] == cc[0]["ContractCommitmentCost"]


def test_conversion_is_deterministic(source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    a = convert_to_focus_1_4(cau, cc, validate=False)
    b = convert_to_focus_1_4(cau, cc, validate=False)
    assert a.datasets == b.datasets
