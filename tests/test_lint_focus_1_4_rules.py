"""Static FOCUS 1.4 Cost and Usage rules enforced by the linter (single-row, SEMANTIC).

Hand-authored partial rows: each assertion follows from the cited v1.4 requirements-model
rule, independent of the converter and the generators.
"""

from __future__ import annotations

import csv
from decimal import Decimal, Inexact, localcontext
from pathlib import Path

import pytest

from focus_data_toolkit.convert import convert_files, convert_to_focus_1_4, read_csv_rows
from focus_data_toolkit.model.capabilities import CapabilityProfile
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
    # The tolerance scales with |cost|, so a large negative (credited) cost behaves the same.
    negative = {"PricingQuantity": "-1000000", "ListUnitPrice": "1234.5678"}
    assert rule not in _rules({**negative, "ListCost": "-1234567800.5"})
    assert rule in _rules({**negative, "ListCost": "-1234567802"})


def test_cost_identity_tolerance_boundary_is_inclusive():
    rule = ("cost_not_unit_price_times_quantity", "ListCost")
    base = {"PricingQuantity": "3", "ListUnitPrice": "0.1"}  # product 0.3, |cost| < 1
    assert rule not in _rules({**base, "ListCost": "0.300000001"})  # exactly 1e-9 off
    assert rule in _rules({**base, "ListCost": "0.3000000010000000001"})


# A product that a 4-digit context rounds (0.370370367 -> 0.3704), and a cost just off it.
EXACT_MATCH = {"PricingQuantity": "3", "ListUnitPrice": "0.123456789", "ListCost": "0.370370367"}
REAL_DIFFERENCE = {"PricingQuantity": "3", "ListUnitPrice": "0.1", "ListCost": "0.30001"}


def test_cost_identity_does_not_depend_on_the_callers_decimal_context():
    rule = ("cost_not_unit_price_times_quantity", "ListCost")
    with localcontext() as ctx:
        ctx.prec = 4
        # Rounding the product would hide a real difference...
        assert rule in _rules(REAL_DIFFERENCE)
        # ...and invent one where the identity holds exactly.
        assert rule not in _rules(EXACT_MATCH)


def test_cost_identity_ignores_a_caller_context_that_traps_inexact():
    rule = ("cost_not_unit_price_times_quantity", "ListCost")
    with localcontext() as ctx:
        ctx.prec = 4
        ctx.traps[Inexact] = True
        assert rule in _rules(REAL_DIFFERENCE)
        assert rule not in _rules(EXACT_MATCH)


@pytest.mark.parametrize(
    "extreme",
    [
        {"ListCost": "9E999999999"},
        {"PricingQuantity": "1E-999999999999999999"},
        {"PricingQuantity": "1E999999999999999999", "ListUnitPrice": "1E999999999999999999"},
        {"ListUnitPrice": "0E-999999999"},
        {"ListCost": "1E1001"},
    ],
)
def test_extreme_exponents_are_not_computable_and_never_computed(extreme):
    # Exact arithmetic on such operands could need billions of digits (a MemoryError): they
    # are reported as not computable before any arithmetic.
    row = {"PricingQuantity": "3", "ListUnitPrice": "0.1", "ListCost": "0.3", **extreme}
    flagged = _rules(row)
    assert ("cost_identity_not_computable", "ListCost") in flagged
    assert ("cost_not_unit_price_times_quantity", "ListCost") not in flagged


def test_large_but_bounded_exponents_are_still_computed_exactly():
    rule = ("cost_not_unit_price_times_quantity", "ListCost")
    # The lint's numeric format writes exponents without a "+" sign.
    base = {"PricingQuantity": "1E500", "ListUnitPrice": "1E500"}
    exact = _rules({**base, "ListCost": "1E1000"})
    assert rule not in exact
    assert ("cost_identity_not_computable", "ListCost") not in exact
    assert rule in _rules({**base, "ListCost": "2E1000"})


def test_an_extreme_amount_does_not_stop_the_conversion(source_tables):
    cau, _ = source_tables[("aws", "1.2")]
    usage = next(r for r in cau if r["ChargeCategory"] == "Usage" and r["PricingQuantity"])
    row = dict(usage, PricingQuantity="1E-999999999999999999")
    for mode in ("strict", "synthetic"):
        report = convert_to_focus_1_4([row], mode=mode).reports[CU]
        assert "cost_identity_not_computable" in {v.rule for v in report.violations}


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
# SkuPriceId rules
# --------------------------------------------------------------------------- #
def test_pricing_columns_must_be_null_without_sku_price_id_under_unit_pricing():
    row = {"ChargeCategory": "Credit", "SkuPriceId": "", "PricingQuantity": "2",
           "PricingUnit": "Hours", "ListUnitPrice": "0.1"}
    flagged = _rules(row, unit_pricing=True)
    assert ("must_be_null_without_sku_price_id", "PricingQuantity") in flagged
    assert ("must_be_null_without_sku_price_id", "ListUnitPrice") in flagged
    # Undeclared condition: never evaluated, never assumed.
    assert not {r for r in _rules(row) if r[0] == "must_be_null_without_sku_price_id"}


@pytest.mark.parametrize(
    ("charge", "charge_class"), [("Usage", ""), ("Purchase", ""), ("Usage", "Correction")]
)
def test_unit_prices_required_with_sku_price_id_in_every_profile(charge, charge_class):
    # CAU-ListUnitPrice-C-013 / CAU-ContractedUnitPrice-C-015 have no applicability criteria
    # in the v1.4 model: they apply whatever conditions the caller declares.
    row = {"ChargeCategory": charge, "ChargeClass": charge_class, "SkuId": "S1",
           "SkuPriceId": "SP1", "ListUnitPrice": "", "ContractedUnitPrice": "0.09"}
    for unit_pricing in (False, True):
        flagged = _rules(row, unit_pricing=unit_pricing)
        assert ("required_with_sku_price_id", "ListUnitPrice") in flagged
        assert ("required_with_sku_price_id", "ContractedUnitPrice") not in flagged


def test_unit_price_rules_skip_a_column_the_source_did_not_carry():
    row = {"ChargeCategory": "Usage", "SkuId": "S1", "SkuPriceId": "SP1",
           "ListUnitPrice": "0.12", "ContractedUnitPrice": ""}
    rule = ("required_with_sku_price_id", "ContractedUnitPrice")
    assert rule in _rules(row)
    report = lint_focus_1_4_structure(CU, [row], source_absent_columns=["ContractedUnitPrice"])
    assert rule not in {(v.rule, v.column) for v in report.violations}
    # A column missing from the rows themselves (e.g. a 1.4 file read from disk) counts too.
    without_column = {k: v for k, v in row.items() if k != "ContractedUnitPrice"}
    assert rule not in _rules(without_column)


def test_converter_tells_the_linter_which_columns_its_source_lacked(source_tables):
    cau, _ = source_tables[("aws", "1.2")]
    usage = next(r for r in cau if r["ChargeCategory"] == "Usage" and r["SkuPriceId"])
    with_column = dict(usage, ContractedUnitPrice="")
    without_column = {k: v for k, v in usage.items() if k != "ContractedUnitPrice"}
    refused = convert_to_focus_1_4([with_column], mode="strict").reports[CU]
    assert ("required_with_sku_price_id", "ContractedUnitPrice") in {
        (v.rule, v.column) for v in refused.violations
    }
    accepted = convert_to_focus_1_4([without_column], mode="strict").reports[CU]
    assert "required_with_sku_price_id" not in {v.rule for v in accepted.violations}


def _write(path: Path, rows: list[dict[str, str]]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_streaming_tells_the_linter_which_columns_its_source_lacked(tmp_path, source_tables):
    # Same plumbing as the eager pipeline: a source without ContractedUnitPrice publishes,
    # while one carrying the column with a null value next to a SkuPriceId is refused.
    cau, _ = source_tables[("aws", "1.2")]
    without_column = [{k: v for k, v in r.items() if k != "ContractedUnitPrice"} for r in cau]
    convert_files(_write(tmp_path / "without.csv", without_column), tmp_path / "out1",
                  mode="strict")
    assert (tmp_path / "out1" / "focus_1_4_manifest.json").exists()
    with_null = [dict(r) for r in cau]
    target = next(r for r in with_null if r["ChargeCategory"] == "Usage" and r["SkuPriceId"])
    target["ContractedUnitPrice"] = ""
    with pytest.raises(Exception, match="lint"):
        convert_files(_write(tmp_path / "with.csv", with_null), tmp_path / "out2", mode="strict")
    assert not (tmp_path / "out2" / "focus_1_4_manifest.json").exists()


def test_client_like_fixture_lints_clean_under_unit_pricing():
    fixture = Path(__file__).parent / "fixtures" / "client_like" / "consolidated_multi_provider_1_3.csv"
    result = convert_to_focus_1_4(
        read_csv_rows(fixture), mode="synthetic",
        capabilities=CapabilityProfile.of(COND_UNIT_PRICING),
    )
    assert result.reports[CU].ok, result.reports[CU].messages()
