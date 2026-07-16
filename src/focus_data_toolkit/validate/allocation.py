"""Validate Split Cost Allocation groups within a Cost and Usage dataset.

An allocation *group* redistributes one origin charge across several consumers. FOCUS carries
the per-line ratio inside ``AllocatedMethodDetails`` (an ``Elements`` array of
``{AllocatedRatio, UsageUnit, UsageQuantity}``). To make a group's origin identifiable and its
origin cost checkable, rows are tied together by the toolkit extension columns
``x_SplitOriginId`` (a stable origin-charge key) and ``x_SplitOriginCost`` (the origin amount,
identical across the group). Rows without ``x_SplitOriginId`` are not allocation rows and are
ignored, so the check is a no-op on datasets that do not use split cost allocation.

Per group it checks: ratios sum to 1 (within tolerance) and each ratio is in [0, 1]; allocated
costs sum to the origin cost (within tolerance); a single consistent method and unit; unique
allocated resources; and that every row carries the information the group needs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation

from focus_data_toolkit.errors import Diagnostic, Severity

Rows = Sequence[Mapping[str, str]]

ORIGIN_ID_COLUMN = "x_SplitOriginId"
ORIGIN_COST_COLUMN = "x_SplitOriginCost"

DEFAULT_RATIO_TOLERANCE = Decimal("0.0001")
DEFAULT_COST_TOLERANCE = Decimal("0.01")


def _dec(value: str | None) -> Decimal | None:
    try:
        return Decimal((value or "").strip())
    except InvalidOperation:
        return None


def _row_ratio_and_unit(row: Mapping[str, str]) -> tuple[Decimal | None, str | None]:
    """Extract (summed AllocatedRatio, UsageUnit) from a row's AllocatedMethodDetails JSON."""
    text = (row.get("AllocatedMethodDetails") or "").strip()
    if not text:
        return None, None
    try:
        obj = json.loads(text, parse_float=Decimal, parse_int=Decimal)
    except (ValueError, TypeError):
        return None, None
    elements = obj.get("Elements") if isinstance(obj, dict) else None
    if not isinstance(elements, list) or not elements:
        return None, None
    ratio = Decimal(0)
    unit: str | None = None
    for el in elements:
        if not isinstance(el, dict):
            return None, None
        raw = el.get("AllocatedRatio")
        if not isinstance(raw, Decimal):
            return None, unit
        ratio += raw
        if unit is None:
            unit = el.get("UsageUnit")
    return ratio, unit


def validate_split_allocation(
    cost_and_usage: Rows,
    *,
    ratio_tolerance: Decimal = DEFAULT_RATIO_TOLERANCE,
    cost_tolerance: Decimal = DEFAULT_COST_TOLERANCE,
) -> list[Diagnostic]:
    """Validate every split-cost-allocation group in ``cost_and_usage``."""
    groups: dict[str, list[tuple[int, Mapping[str, str]]]] = {}
    for i, row in enumerate(cost_and_usage, start=1):
        origin = (row.get(ORIGIN_ID_COLUMN) or "").strip()
        if origin:
            groups.setdefault(origin, []).append((i, row))

    out: list[Diagnostic] = []
    for origin_id, members in sorted(groups.items()):
        out.extend(_validate_group(origin_id, members, ratio_tolerance, cost_tolerance))
    return out


def _validate_group(
    origin_id: str,
    members: list[tuple[int, Mapping[str, str]]],
    ratio_tolerance: Decimal,
    cost_tolerance: Decimal,
) -> list[Diagnostic]:
    keys = {ORIGIN_ID_COLUMN: origin_id}
    lines = sorted(i for i, _ in members)
    out: list[Diagnostic] = []

    def incomplete(reason: str, column: str | None = None) -> Diagnostic:
        return Diagnostic(
            code="FDT-ALLOC-005",
            severity=Severity.ERROR,
            message=f"split allocation group {origin_id!r} is incomplete: {reason}",
            datasets=("Cost and Usage",),
            dataset="Cost and Usage",
            column=column,
            record_keys=keys,
            context={"rows": ",".join(map(str, lines))},
        )

    ratios: list[Decimal] = []
    units: set[str] = set()
    methods: set[str] = set()
    resources: list[str] = []
    total_cost = Decimal(0)
    origin_costs: set[Decimal] = set()

    for _i, row in members:
        ratio, unit = _row_ratio_and_unit(row)
        if ratio is None:
            return [incomplete("a row has no usable AllocatedRatio", "AllocatedMethodDetails")]
        ratios.append(ratio)
        if unit:
            units.add(unit)
        methods.add((row.get("AllocatedMethodId") or "").strip())
        resources.append((row.get("AllocatedResourceId") or "").strip())
        cost = _dec(row.get("BilledCost"))
        if cost is None:
            return [incomplete("a row has no numeric BilledCost", "BilledCost")]
        total_cost += cost
        origin_cost = _dec(row.get(ORIGIN_COST_COLUMN))
        if origin_cost is None:
            return [incomplete("missing x_SplitOriginCost", ORIGIN_COST_COLUMN)]
        origin_costs.add(origin_cost)

    if "" in methods or any(not r for r in resources):
        return [incomplete("a row is missing AllocatedMethodId or AllocatedResourceId")]
    if len(origin_costs) != 1:
        return [incomplete("rows disagree on x_SplitOriginCost", ORIGIN_COST_COLUMN)]

    for ratio in ratios:
        if ratio < 0 or ratio > 1:
            out.append(
                Diagnostic(
                    code="FDT-ALLOC-006",
                    severity=Severity.ERROR,
                    message=f"allocation ratio {ratio} outside [0, 1] in group {origin_id!r}",
                    datasets=("Cost and Usage",),
                    dataset="Cost and Usage",
                    column="AllocatedMethodDetails",
                    actual=str(ratio),
                    record_keys=keys,
                )
            )

    ratio_sum = sum(ratios, Decimal(0))
    if abs(ratio_sum - Decimal(1)) > ratio_tolerance:
        out.append(
            Diagnostic(
                code="FDT-ALLOC-001",
                severity=Severity.ERROR,
                message=f"allocation ratios in group {origin_id!r} sum to {ratio_sum}, not 1",
                datasets=("Cost and Usage",),
                dataset="Cost and Usage",
                column="AllocatedMethodDetails",
                expected="1",
                actual=str(ratio_sum),
                record_keys=keys,
            )
        )

    origin_cost = next(iter(origin_costs))
    if abs(total_cost - origin_cost) > cost_tolerance:
        out.append(
            Diagnostic(
                code="FDT-ALLOC-002",
                severity=Severity.ERROR,
                message=f"allocated costs in group {origin_id!r} sum to {total_cost}, not the "
                f"origin cost {origin_cost}",
                datasets=("Cost and Usage",),
                dataset="Cost and Usage",
                column="BilledCost",
                expected=str(origin_cost),
                actual=str(total_cost),
                record_keys=keys,
            )
        )

    if len({m for m in methods if m}) > 1:
        out.append(
            Diagnostic(
                code="FDT-ALLOC-003",
                severity=Severity.ERROR,
                message=f"inconsistent AllocatedMethodId within group {origin_id!r}: "
                f"{sorted(methods)}",
                datasets=("Cost and Usage",),
                dataset="Cost and Usage",
                column="AllocatedMethodId",
                record_keys=keys,
            )
        )

    if len(units) > 1:
        out.append(
            Diagnostic(
                code="FDT-ALLOC-007",
                severity=Severity.ERROR,
                message=f"inconsistent UsageUnit within group {origin_id!r}: {sorted(units)}",
                datasets=("Cost and Usage",),
                dataset="Cost and Usage",
                column="AllocatedMethodDetails",
                record_keys=keys,
            )
        )

    if len(set(resources)) != len(resources):
        out.append(
            Diagnostic(
                code="FDT-ALLOC-004",
                severity=Severity.ERROR,
                message=f"duplicate AllocatedResourceId within group {origin_id!r}",
                datasets=("Cost and Usage",),
                dataset="Cost and Usage",
                column="AllocatedResourceId",
                record_keys=keys,
            )
        )

    return out
