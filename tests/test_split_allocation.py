"""Split Cost Allocation validation (P1.8). Hand-authored + generator-built allocation groups."""

from __future__ import annotations

from decimal import Decimal

import pytest

from focus_data_toolkit.generators.scenarios import split_allocation_group
from focus_data_toolkit.validate.allocation import validate_split_allocation


def alloc(
    origin_id: str,
    origin_cost: str,
    ratio: str,
    cost: str,
    resource: str,
    *,
    method: str = "split-proportional",
    unit: str = "Hours",
) -> dict[str, str]:
    details = (
        '{"Elements":[{"AllocatedRatio":' + ratio + ',"UsageUnit":"' + unit + '","UsageQuantity":1}]}'
    )
    return {
        "x_SplitOriginId": origin_id,
        "x_SplitOriginCost": origin_cost,
        "AllocatedMethodId": method,
        "AllocatedResourceId": resource,
        "AllocatedMethodDetails": details,
        "BilledCost": cost,
    }


def codes(rows) -> set[str]:
    return {d.code for d in validate_split_allocation(rows)}


def test_equal_allocation_is_valid():
    rows = [alloc("G1", "100.00", "0.25", "25.00", f"r{i}") for i in range(4)]
    assert validate_split_allocation(rows) == []


def test_proportional_allocation_reconciles():
    rows = [
        alloc("G1", "100.00", "0.45", "45.00", "a"),
        alloc("G1", "100.00", "0.30", "30.00", "b"),
        alloc("G1", "100.00", "0.25", "25.00", "c"),
    ]
    assert validate_split_allocation(rows) == []


def test_rounding_residue_is_absorbed():
    # 100.00 split three ways: last element takes the residue so both sums stay exact.
    rows = [
        alloc("G1", "100.00", "0.333333", "33.33", "a"),
        alloc("G1", "100.00", "0.333333", "33.33", "b"),
        alloc("G1", "100.00", "0.333334", "33.34", "c"),
    ]
    assert validate_split_allocation(rows) == []


def test_ratios_not_summing_to_one():
    rows = [alloc("G1", "100.00", "0.5", "50.00", "a"), alloc("G1", "100.00", "0.3", "30.00", "b")]
    assert "FDT-ALLOC-001" in codes(rows)


def test_costs_do_not_reconcile_to_origin():
    rows = [alloc("G1", "100.00", "0.5", "40.00", "a"), alloc("G1", "100.00", "0.5", "40.00", "b")]
    assert "FDT-ALLOC-002" in codes(rows)


def test_ratio_out_of_range():
    rows = [alloc("G1", "100.00", "1.5", "150.00", "a"), alloc("G1", "100.00", "-0.5", "-50.00", "b")]
    assert "FDT-ALLOC-006" in codes(rows)


def test_incomplete_group_missing_origin_cost():
    row = alloc("G1", "100.00", "1.0", "100.00", "a")
    del row["x_SplitOriginCost"]
    assert "FDT-ALLOC-005" in codes([row])


def test_inconsistent_method():
    rows = [
        alloc("G1", "100.00", "0.5", "50.00", "a", method="split-even"),
        alloc("G1", "100.00", "0.5", "50.00", "b", method="split-proportional"),
    ]
    assert "FDT-ALLOC-003" in codes(rows)


def test_duplicate_allocated_resource():
    rows = [alloc("G1", "100.00", "0.5", "50.00", "same"), alloc("G1", "100.00", "0.5", "50.00", "same")]
    assert "FDT-ALLOC-004" in codes(rows)


def test_negative_correction_allocation_reconciles():
    # A correction that redistributes a negative origin charge is valid.
    rows = [
        alloc("G1", "-100.00", "0.5", "-50.00", "a"),
        alloc("G1", "-100.00", "0.5", "-50.00", "b"),
    ]
    assert validate_split_allocation(rows) == []


def test_mixed_units_across_elements_in_one_row():
    details = (
        '{"Elements":[{"AllocatedRatio":0.5,"UsageUnit":"Hours"},'
        '{"AllocatedRatio":0.5,"UsageUnit":"GB"}]}'
    )
    row = {
        "x_SplitOriginId": "G1",
        "x_SplitOriginCost": "100.00",
        "AllocatedMethodId": "split-even",
        "AllocatedResourceId": "a",
        "AllocatedMethodDetails": details,
        "BilledCost": "100.00",
    }
    assert "FDT-ALLOC-007" in codes([row])


def test_non_finite_ratio_is_flagged_incomplete():
    row = alloc("G1", "100.00", "1.0", "100.00", "a")
    row["AllocatedMethodDetails"] = '{"Elements":[{"AllocatedRatio":Infinity,"UsageUnit":"Hours"}]}'
    assert "FDT-ALLOC-005" in codes([row])


def test_no_allocation_rows_is_a_noop():
    assert validate_split_allocation([{"BilledCost": "10.00", "ChargeCategory": "Usage"}]) == []


# --- generator-built groups (P1.8): coherent scenarios that must validate clean ------------


@pytest.mark.parametrize(
    "weights",
    [
        [1, 1, 1, 1],                 # equal, 4 consumers
        [3, 2, 1],                    # proportional
        [1, 1, 1],                    # 100/3 -> residue must be absorbed
        [7, 11, 13, 17, 19],          # 5 consumers, awkward ratios
        [1],                          # degenerate single consumer
    ],
)
def test_generated_group_reconciles(weights):
    rows = split_allocation_group("G", "100.00", weights)
    assert validate_split_allocation(rows) == []


def test_generated_group_costs_sum_exactly_to_origin():
    rows = split_allocation_group("G", "100.00", [1, 1, 1])
    total = sum((Decimal(r["BilledCost"]) for r in rows), Decimal(0))
    assert total == Decimal("100.00")  # residue absorbed -> exact, no lost cent


def test_generated_negative_origin_reconciles():
    rows = split_allocation_group("G", "-250.75", [2, 3, 5])
    assert validate_split_allocation(rows) == []
    assert sum((Decimal(r["BilledCost"]) for r in rows), Decimal(0)) == Decimal("-250.75")


def test_generated_weighted_group_has_unique_resources():
    rows = split_allocation_group("G", "500.00", [5, 3, 2], resource_prefix="vm")
    assert len({r["AllocatedResourceId"] for r in rows}) == len(rows)


def test_generator_is_deterministic():
    a = split_allocation_group("G", "100.00", [3, 2, 1])
    b = split_allocation_group("G", "100.00", [3, 2, 1])
    assert a == b
