"""Split Cost Allocation validation (P1.8). Hand-authored allocation groups."""

from __future__ import annotations

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


def test_no_allocation_rows_is_a_noop():
    assert validate_split_allocation([{"BilledCost": "10.00", "ChargeCategory": "Usage"}]) == []
