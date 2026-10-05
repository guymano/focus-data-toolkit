"""1.2/1.3 values migrated (or refused) to meet FOCUS 1.4 Cost and Usage rules.

Each case is a hand-made row derived from a generated 1.2 source row, so the expected
output follows from the FOCUS 1.4 rule alone, never from the converter under test.
"""

from __future__ import annotations

import pytest

from focus_data_toolkit.convert import ConversionError, convert_to_focus_1_4
from focus_data_toolkit.convert.cost_and_usage import (
    NULL_WHEN_SKU_PRICE_ID_NULL,
    UNIT_OF_QUANTITY,
    CostAndUsageMigrationError,
)
from focus_data_toolkit.validate.codes import CATALOG

CU = "Cost and Usage"
_CASCADE = (*NULL_WHEN_SKU_PRICE_ID_NULL, *UNIT_OF_QUANTITY.values())


@pytest.fixture
def usage_row(source_tables) -> dict[str, str]:
    cau, _ = source_tables[("aws", "1.2")]
    row = next(
        r for r in cau
        if r["ChargeCategory"] == "Usage" and r["SkuPriceId"] and r["PricingQuantity"]
        and r["ConsumedQuantity"] and r["ListUnitPrice"]
        and r["PricingCurrency"] == r["BillingCurrency"]
    )
    return dict(row)


@pytest.fixture
def tax_row(usage_row) -> dict[str, str]:
    """A Tax charge whose 1.3-style EffectiveCost (7.5) differs from its BilledCost (10)."""
    row = dict(usage_row)
    row.update({
        "ChargeCategory": "Tax", "ChargeDescription": "Tax", "SkuId": "", "SkuPriceId": "",
        "SkuPriceDetails": "", "BilledCost": "10", "EffectiveCost": "7.5",
        "PricingCurrency": row["BillingCurrency"], "PricingCurrencyEffectiveCost": "7.5",
    })
    for col in _CASCADE:
        row[col] = ""  # isolate the Tax rule from the SkuPriceId cascade
    return row


def _codes(result, prefix: str = "FDT-MIG") -> list[str]:
    return [d.code for d in result.diagnostics if d.code.startswith(prefix)]


def _one(result, code: str):
    [diag] = [d for d in result.diagnostics if d.code == code]
    return diag


def test_every_migration_code_is_catalogued():
    for code in ("FDT-MIG-001", "FDT-MIG-002", "FDT-MIG-003", "FDT-MIG-010", "FDT-MIG-011"):
        assert code in CATALOG


def test_tax_effective_cost_is_set_to_billed_cost(usage_row, tax_row):
    result = convert_to_focus_1_4([usage_row, tax_row], mode="strict")
    usage, tax = result.datasets[CU]
    assert tax["EffectiveCost"] == "10"
    # Same pricing and billing currency: the pricing-currency amount follows exactly.
    assert tax["PricingCurrencyEffectiveCost"] == "10"
    assert usage["EffectiveCost"] == usage_row["EffectiveCost"]
    diag = _one(result, "FDT-MIG-001")
    currency = tax_row["BillingCurrency"]
    assert diag.context == {
        "rows_by_currency": f"{currency}:1",
        "effective_cost_delta_by_currency": f"{currency}:2.5",
    }
    summary = result.manifest["datasets"][CU]["lineage_summary"]
    assert summary["EffectiveCost"] == {"DERIVED": 1, "OBSERVED": 1}
    assert summary["PricingCurrencyEffectiveCost"] == {"DERIVED": 1, "OBSERVED": 1}
    assert result.reports[CU].ok, result.reports[CU].messages()[:5]


def test_null_tax_effective_cost_takes_billed_cost(tax_row):
    tax_row.update({"EffectiveCost": "", "PricingCurrencyEffectiveCost": ""})
    result = convert_to_focus_1_4([tax_row], mode="strict")
    [tax] = result.datasets[CU]
    assert tax["EffectiveCost"] == tax["PricingCurrencyEffectiveCost"] == "10"
    assert _one(result, "FDT-MIG-001").context["effective_cost_delta_by_currency"].endswith(":10")


def test_numerically_equal_tax_amounts_keep_their_source_text(tax_row):
    tax_row.update({"EffectiveCost": "10.00", "PricingCurrencyEffectiveCost": "10.00"})
    result = convert_to_focus_1_4([tax_row], mode="strict")
    [tax] = result.datasets[CU]
    assert tax["EffectiveCost"] == "10.00"
    assert "FDT-MIG-001" not in _codes(result)


def test_credit_effective_cost_is_not_rewritten(tax_row):
    # FOCUS 1.4 also requires EffectiveCost == BilledCost for Credit, but that already held in
    # 1.2/1.3 for charges unrelated to others: a violation is a source defect to report, not a
    # value to migrate.
    tax_row.update({"ChargeCategory": "Credit", "BilledCost": "-10"})
    result = convert_to_focus_1_4([tax_row], mode="strict")
    [credit] = result.datasets[CU]
    assert credit["EffectiveCost"] == "7.5"
    assert "FDT-MIG-001" not in _codes(result)


def test_tax_change_in_another_pricing_currency_is_refused(tax_row):
    other = "EUR" if tax_row["BillingCurrency"] != "EUR" else "USD"
    tax_row.update({"PricingCurrency": other, "PricingCurrencyEffectiveCost": "6.9"})
    with pytest.raises(CostAndUsageMigrationError, match="FDT-MIG-010") as exc:
        convert_to_focus_1_4([tax_row], mode="strict")
    assert isinstance(exc.value, ConversionError)
    assert "BillingAccountId=" in str(exc.value)


def test_null_pricing_effective_cost_is_not_backfilled_across_currencies(usage_row):
    other = "EUR" if usage_row["BillingCurrency"] != "EUR" else "USD"
    usage_row.update({"PricingCurrency": other, "PricingCurrencyEffectiveCost": ""})
    with pytest.raises(CostAndUsageMigrationError, match="FDT-MIG-011"):
        convert_to_focus_1_4([usage_row], mode="strict")


def test_pricing_columns_are_nulled_where_sku_price_id_is_null(usage_row):
    credit = dict(usage_row)
    credit.update({"ChargeCategory": "Credit", "SkuPriceId": "", "SkuPriceDetails": ""})
    carried = [c for c in NULL_WHEN_SKU_PRICE_ID_NULL if credit.get(c)]
    assert {"ListUnitPrice", "PricingQuantity", "ConsumedQuantity"} <= set(carried)
    result = convert_to_focus_1_4([usage_row, credit], mode="strict")
    kept, nulled = result.datasets[CU]
    for col in _CASCADE:
        assert nulled[col] == "", col
    # A unit follows its quantity; a row with a SkuPriceId keeps every value.
    for col in carried:
        assert kept[col] == usage_row[col], col
    counts = dict(
        part.split(":")
        for part in _one(result, "FDT-MIG-002").context["values_by_column"].split("; ")
    )
    expected = set(carried) | {
        UNIT_OF_QUANTITY[q] for q in carried if q in UNIT_OF_QUANTITY and credit.get(UNIT_OF_QUANTITY[q])
    }
    assert set(counts) == expected and set(counts.values()) == {"1"}
    summary = result.manifest["datasets"][CU]["lineage_summary"]
    assert summary["PricingQuantity"] == {"DERIVED": 1, "OBSERVED": 1}


def test_no_cascade_when_the_source_has_no_sku_price_id_column(usage_row):
    row = {k: v for k, v in usage_row.items() if k not in ("SkuPriceId", "SkuPriceDetails")}
    result = convert_to_focus_1_4([row], mode="strict")
    [out] = result.datasets[CU]
    assert out["PricingQuantity"] == usage_row["PricingQuantity"]
    assert "FDT-MIG-002" not in _codes(result)
    assert "PricingQuantity" not in result.manifest["datasets"][CU]["lineage_summary"]


def test_source_null_pricing_currency_is_backfilled_with_a_warning(usage_row):
    usage_row.update({"PricingCurrency": "", "PricingCurrencyEffectiveCost": ""})
    result = convert_to_focus_1_4([usage_row], mode="strict")
    [out] = result.datasets[CU]
    assert out["PricingCurrency"] == usage_row["BillingCurrency"]
    assert out["PricingCurrencyEffectiveCost"] == usage_row["EffectiveCost"]
    assert _one(result, "FDT-MIG-003").context == {
        "values_by_column": "PricingCurrency:1; PricingCurrencyEffectiveCost:1"
    }


def test_absent_pricing_currency_columns_are_backfilled_without_a_warning(usage_row):
    # A provider that never prices in another currency omits the columns: the billing currency
    # is the pricing currency by the column's own presence condition.
    row = {
        k: v for k, v in usage_row.items()
        if not k.startswith("PricingCurrency")
    }
    result = convert_to_focus_1_4([row], mode="strict")
    [out] = result.datasets[CU]
    assert out["PricingCurrency"] == usage_row["BillingCurrency"]
    assert "FDT-MIG-003" not in _codes(result)


def test_generated_sources_need_no_migration(source_tables):
    for (provider, version), (cau, _cc) in source_tables.items():
        result = convert_to_focus_1_4(cau, mode="strict")
        assert _codes(result) == [], (provider, version)
