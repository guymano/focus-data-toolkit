"""Multi-provider / multi-issuer handling (P1.3).

All fixtures here are hand-authored consolidated exports (not produced by the internal
generators), so they exercise the toolkit on genuinely heterogeneous input.
"""

from __future__ import annotations

from decimal import Decimal

from focus_data_toolkit.context import (
    describe_source_contexts,
    distinct_provider_contexts,
)
from focus_data_toolkit.convert import convert_to_focus_1_4
from focus_data_toolkit.convert.billing_period import build_billing_periods
from focus_data_toolkit.convert.invoice_detail import build_invoice_details
from focus_data_toolkit.modes import Mode

P1 = "2026-05-01T00:00:00Z"
P2 = "2026-06-01T00:00:00Z"


def cau(**over: str) -> dict[str, str]:
    base = {
        "ServiceProviderName": "AWS",
        "HostProviderName": "AWS",
        "InvoiceIssuerName": "AWS",
        "InvoiceId": "INV-1",
        "BillingAccountId": "BA-1",
        "BillingCurrency": "USD",
        "BillingPeriodStart": P1,
        "BillingPeriodEnd": P2,
        "ChargeCategory": "Usage",
        "BilledCost": "10.00",
        "EffectiveCost": "10.00",
    }
    base.update(over)
    return base


def test_two_issuers_same_invoice_id_are_not_merged():
    rows = [
        cau(InvoiceIssuerName="Provider A", BilledCost="10.00"),
        cau(InvoiceIssuerName="Provider B", BilledCost="20.00"),
    ]
    details, mapping = build_invoice_details(rows)
    assert len(details) == 2
    issuers = {d["InvoiceIssuerName"]: d["BilledCost"] for d in details}
    assert issuers == {"Provider A": "10.000000", "Provider B": "20.000000"}
    # Distinct grain -> distinct locally generated ids (no collision across issuers).
    assert len({d["InvoiceDetailId"] for d in details}) == 2


def test_two_accounts_are_not_merged():
    rows = [cau(BillingAccountId="BA-1"), cau(BillingAccountId="BA-2")]
    details, _ = build_invoice_details(rows)
    assert {d["BillingAccountId"] for d in details} == {"BA-1", "BA-2"}


def test_two_currencies_are_not_merged():
    rows = [cau(BillingCurrency="USD"), cau(BillingCurrency="EUR")]
    details, _ = build_invoice_details(rows)
    assert {d["BillingCurrency"] for d in details} == {"USD", "EUR"}


def test_two_periods_are_not_merged():
    rows = [
        cau(BillingPeriodStart=P1, BillingPeriodEnd=P2),
        cau(BillingPeriodStart=P2, BillingPeriodEnd="2026-07-01T00:00:00Z"),
    ]
    details, _ = build_invoice_details(rows)
    assert len(details) == 2


def test_same_grain_aggregates_and_reconciles():
    rows = [cau(BilledCost="10.00"), cau(BilledCost="5.25"), cau(BilledCost="0.75")]
    details, _ = build_invoice_details(rows)
    assert len(details) == 1
    assert Decimal(details[0]["BilledCost"]) == Decimal("16.000000")


def test_provider_context_is_per_row_not_first_row():
    rows = [cau(ServiceProviderName="AWS", HostProviderName="AWS"),
            cau(ServiceProviderName="Datadog", HostProviderName="AWS")]
    contexts = distinct_provider_contexts(rows, "1.3")
    assert len(contexts) == 2
    services = {c.service_provider_name for c in contexts}
    assert services == {"AWS", "Datadog"}


def test_billing_period_issuer_not_backfilled_from_first_row():
    # Second row has no issuer: it must NOT inherit "Provider A" from the first row.
    rows = [
        cau(InvoiceIssuerName="Provider A"),
        cau(InvoiceIssuerName="", InvoiceId="INV-2", BillingAccountId="BA-2"),
    ]
    periods = build_billing_periods(rows)
    issuers = {p["InvoiceIssuerName"] for p in periods}
    assert issuers == {"Provider A", ""}


def test_context_summary_flags_heterogeneity():
    rows = [
        cau(ServiceProviderName="AWS", InvoiceIssuerName="AWS", BillingCurrency="USD"),
        cau(ServiceProviderName="Microsoft Azure", InvoiceIssuerName="Microsoft",
            BillingCurrency="EUR", InvoiceId="INV-9", BillingAccountId="sub-9"),
    ]
    summary = describe_source_contexts(rows, "1.3")
    assert summary["multi_provider"] is True
    assert summary["multi_issuer"] is True
    assert summary["multi_currency"] is True
    assert summary["billing_currencies"]["count"] == 2


def test_consolidated_convert_keeps_issuers_separate():
    rows = [
        cau(InvoiceIssuerName="AWS", ServiceProviderName="AWS", HostProviderName="AWS"),
        cau(InvoiceIssuerName="Microsoft", ServiceProviderName="Microsoft Azure",
            HostProviderName="Microsoft Azure", BilledCost="30.00"),
    ]
    result = convert_to_focus_1_4(rows, source_version="1.3", mode=Mode.SYNTHETIC, validate=False)
    details = result.datasets["Invoice Detail"]
    assert {d["InvoiceIssuerName"] for d in details} == {"AWS", "Microsoft"}
    assert result.contexts["multi_issuer"] is True
    # Every Cost and Usage row still back-links to its own invoice line.
    detail_ids = {d["InvoiceDetailId"] for d in details}
    for row in result.datasets["Cost and Usage"]:
        assert row["InvoiceDetailId"] in detail_ids
