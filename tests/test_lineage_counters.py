"""Per-value lineage counters (``lineage_summary`` in the manifest).

The pricing-currency backfill pair keeps a conservative column-level ``DERIVED``
label, but the manifest now also records how many values were actually observed
vs. backfilled, so the column label never hides the per-value mix.
"""

from __future__ import annotations

from focus_data_toolkit.convert import convert_to_focus_1_4
from focus_data_toolkit.manifest import render
from focus_data_toolkit.modes import Mode
from focus_data_toolkit.provenance import Lineage, LineageCounters


def cau(**over: str) -> dict[str, str]:
    base = {
        "ServiceProviderName": "AWS",
        "HostProviderName": "AWS",
        "InvoiceIssuerName": "AWS",
        "InvoiceId": "INV-1",
        "BillingAccountId": "BA-1",
        "BillingCurrency": "USD",
        "BillingPeriodStart": "2026-05-01T00:00:00Z",
        "BillingPeriodEnd": "2026-06-01T00:00:00Z",
        "ChargeCategory": "Usage",
        "BilledCost": "10.00",
        "EffectiveCost": "10.00",
        "PricingCurrency": "USD",
        "PricingCurrencyEffectiveCost": "10.00",
    }
    base.update(over)
    return base


def test_counters_accumulate_and_summarize_deterministically():
    counters = LineageCounters()
    counters.record("PricingCurrency", Lineage.OBSERVED, 3)
    counters.record("PricingCurrency", Lineage.DERIVED, 2)
    assert counters.summary() == {"PricingCurrency": {"DERIVED": 2, "OBSERVED": 3}}
    assert bool(counters) is True
    assert bool(LineageCounters()) is False


def test_manifest_lineage_summary_counts_observed_vs_backfilled():
    rows = [
        cau(),
        cau(),
        cau(),
        cau(PricingCurrency="", PricingCurrencyEffectiveCost=""),
        cau(PricingCurrency="", PricingCurrencyEffectiveCost=""),
    ]
    result = convert_to_focus_1_4(rows, source_version="1.3", mode=Mode.SYNTHETIC, validate=False)
    entry = result.manifest["datasets"]["Cost and Usage"]
    assert entry["lineage_summary"]["PricingCurrency"] == {"DERIVED": 2, "OBSERVED": 3}
    assert entry["lineage_summary"]["PricingCurrencyEffectiveCost"] == {
        "DERIVED": 2,
        "OBSERVED": 3,
    }
    # The headline column lineage stays the conservative (weakest) category.
    assert entry["columns"]["PricingCurrency"]["lineage"] == "DERIVED"


def test_backfilled_values_equal_billing_currency_values():
    rows = [cau(PricingCurrency="", PricingCurrencyEffectiveCost="")]
    result = convert_to_focus_1_4(rows, source_version="1.3", mode=Mode.SYNTHETIC, validate=False)
    out = result.datasets["Cost and Usage"][0]
    assert out["PricingCurrency"] == "USD"
    assert out["PricingCurrencyEffectiveCost"] == "10.00"


def test_manifest_stays_deterministic_with_summary():
    rows = [cau(), cau(PricingCurrency="")]
    a = convert_to_focus_1_4(rows, source_version="1.3", mode=Mode.SYNTHETIC, validate=False).manifest
    b = convert_to_focus_1_4(rows, source_version="1.3", mode=Mode.SYNTHETIC, validate=False).manifest
    assert render(a) == render(b)
