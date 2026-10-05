"""Static FOCUS 1.4 Cost and Usage rules enforced by the linter (single-row, SEMANTIC).

Hand-authored partial rows: each assertion follows from the cited v1.4 requirements-model
rule, independent of the converter and the generators.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from focus_data_toolkit.model.validator import (
    COND_UNIT_PRICING,
    LEVEL_SEMANTIC,
    lint_focus_1_4_structure,
)

CU = "Cost and Usage"


def _rules(row: dict[str, str], *, unit_pricing: bool = False) -> set[tuple[str, str]]:
    supported = [COND_UNIT_PRICING] if unit_pricing else []
    report = lint_focus_1_4_structure(CU, [row], supported_conditions=supported)
    return {(v.rule, v.column) for v in report.violations if v.level == LEVEL_SEMANTIC}


# --------------------------------------------------------------------------- #
# CAU-EffectiveCost-C-017
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("charge", ["Tax", "Credit"])
def test_tax_and_credit_effective_cost_must_equal_billed_cost(charge):
    bad = {"ChargeCategory": charge, "BilledCost": "10", "EffectiveCost": "7.5"}
    assert ("effective_cost_differs_from_billed", "EffectiveCost") in _rules(bad)
    equal = {"ChargeCategory": charge, "BilledCost": "10", "EffectiveCost": "10.00"}
    assert ("effective_cost_differs_from_billed", "EffectiveCost") not in _rules(equal)


def test_usage_effective_cost_may_differ_from_billed_cost():
    row = {"ChargeCategory": "Usage", "BilledCost": "0", "EffectiveCost": "7.5"}
    assert ("effective_cost_differs_from_billed", "EffectiveCost") not in _rules(row)


# --------------------------------------------------------------------------- #
# CAU-ListCost-C-011 / CAU-ContractedCost-C-011 (tolerance of the official validator)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("cost_col", "price_col"),
    [("ListCost", "ListUnitPrice"), ("ContractedCost", "ContractedUnitPrice")],
)
def test_cost_must_equal_unit_price_times_quantity(cost_col, price_col):
    rule = ("cost_not_unit_price_times_quantity", cost_col)
    base = {"ChargeCategory": "Usage", "PricingQuantity": "3", price_col: "0.1"}
    assert rule not in _rules({**base, cost_col: "0.3"})
    # Since 1.3 the identity also holds on Correction rows.
    assert rule in _rules({**base, cost_col: "0.31", "ChargeClass": "Correction"})
    # Representation rounding inside 1e-9 x max(|cost|, 1) is not a violation...
    assert rule not in _rules({**base, cost_col: str(Decimal("0.3") + Decimal("5E-10"))})
    # ...one just beyond it is.
    assert rule in _rules({**base, cost_col: str(Decimal("0.3") + Decimal("2E-9"))})


def test_cost_identity_needs_both_factors():
    row = {"ChargeCategory": "Usage", "PricingQuantity": "", "ListUnitPrice": "0.1",
           "ListCost": "99"}
    assert ("cost_not_unit_price_times_quantity", "ListCost") not in _rules(row)


def test_cost_identity_tolerance_scales_with_large_costs():
    rule = ("cost_not_unit_price_times_quantity", "ListCost")
    # 1e-9 relative on a 1.23e9 cost allows ~1.23: 0.5 off passes, 2 off fails.
    row = {"PricingQuantity": "1000000", "ListUnitPrice": "1234.5678",
           "ListCost": "1234567800.5"}
    assert rule not in _rules(row)
    assert rule in _rules({**row, "ListCost": "1234567802"})


# --------------------------------------------------------------------------- #
# CAU-*Unit-C-005/006: a unit is null exactly when its quantity is
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("quantity_col", "unit_col"),
    [("PricingQuantity", "PricingUnit"), ("ConsumedQuantity", "ConsumedUnit"),
     ("CommitmentDiscountQuantity", "CommitmentDiscountUnit")],
)
def test_unit_follows_quantity(quantity_col, unit_col):
    usage = {"ChargeCategory": "Usage"}
    assert ("quantity_without_unit", unit_col) in _rules({**usage, quantity_col: "2"})
    assert ("unit_without_quantity", unit_col) in _rules({**usage, unit_col: "Hours"})
    paired = {**usage, quantity_col: "2", unit_col: "Hours"}
    assert not {r for r in _rules(paired) if r[1] == unit_col}


# --------------------------------------------------------------------------- #
# SkuPriceId rules, evaluated only under declared unit pricing
# --------------------------------------------------------------------------- #
def test_pricing_columns_must_be_null_without_sku_price_id_under_unit_pricing():
    row = {"ChargeCategory": "Credit", "SkuPriceId": "", "PricingQuantity": "2",
           "PricingUnit": "Hours", "ListUnitPrice": "0.1"}
    flagged = _rules(row, unit_pricing=True)
    assert ("must_be_null_without_sku_price_id", "PricingQuantity") in flagged
    assert ("must_be_null_without_sku_price_id", "ListUnitPrice") in flagged
    # Undeclared condition: never evaluated, never assumed.
    assert not {r for r in _rules(row) if r[0] == "must_be_null_without_sku_price_id"}


def test_unit_prices_required_with_sku_price_id_under_unit_pricing():
    row = {"ChargeCategory": "Usage", "SkuId": "S1", "SkuPriceId": "SP1",
           "ListUnitPrice": "", "ContractedUnitPrice": "0.09"}
    flagged = _rules(row, unit_pricing=True)
    assert ("required_with_sku_price_id", "ListUnitPrice") in flagged
    assert ("required_with_sku_price_id", "ContractedUnitPrice") not in flagged
    assert ("required_with_sku_price_id", "ListUnitPrice") not in _rules(row)
