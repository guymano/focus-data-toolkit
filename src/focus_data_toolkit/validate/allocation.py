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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from focus_data_toolkit.errors import Diagnostic, Severity

Rows = Sequence[Mapping[str, str]]
#: The input is consumed in a single forward pass, so any iterable of rows works.
RowStream = Iterable[Mapping[str, str]]

ORIGIN_ID_COLUMN = "x_SplitOriginId"
ORIGIN_COST_COLUMN = "x_SplitOriginCost"

DEFAULT_RATIO_TOLERANCE = Decimal("0.0001")
DEFAULT_COST_TOLERANCE = Decimal("0.01")


def _dec(value: str | None) -> Decimal | None:
    try:
        parsed = Decimal((value or "").strip())
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _row_ratio_and_units(row: Mapping[str, str]) -> tuple[Decimal | None, frozenset[str]]:
    """Extract (summed AllocatedRatio, set of UsageUnits) from a row's AllocatedMethodDetails.

    Every element's ``UsageUnit`` is collected — not just the first — so a row mixing units
    across its elements is caught by the group's single-unit check.
    """
    text = (row.get("AllocatedMethodDetails") or "").strip()
    if not text:
        return None, frozenset()
    try:
        obj = json.loads(text, parse_float=Decimal, parse_int=Decimal)
    except (ValueError, TypeError):
        return None, frozenset()
    elements = obj.get("Elements") if isinstance(obj, dict) else None
    if not isinstance(elements, list) or not elements:
        return None, frozenset()
    ratio = Decimal(0)
    units: set[str] = set()
    for el in elements:
        if not isinstance(el, dict):
            return None, frozenset(units)
        raw = el.get("AllocatedRatio")
        if not isinstance(raw, Decimal) or not raw.is_finite():
            return None, frozenset(units)
        ratio += raw
        unit = el.get("UsageUnit")
        if unit:
            units.add(unit)
    return ratio, frozenset(units)


@dataclass
class _GroupState:
    """Running aggregate of one allocation group — bounded per group, never member rows.

    Mirrors the checks of the previous list-of-members implementation exactly: the first
    incomplete condition (in row order) short-circuits further accumulation, line numbers
    keep accumulating so the incomplete diagnostic still lists every member row.
    """

    lines: list[int] = field(default_factory=list)
    incomplete: tuple[str, str | None] | None = None  # (reason, column)
    ratios: list[Decimal] = field(default_factory=list)
    units: set[str] = field(default_factory=set)
    methods: set[str] = field(default_factory=set)
    resource_count: int = 0
    resources: set[str] = field(default_factory=set)
    missing_resource: bool = False
    total_cost: Decimal = Decimal(0)
    origin_costs: set[Decimal] = field(default_factory=set)

    def observe(self, line: int, row: Mapping[str, str]) -> None:
        self.lines.append(line)
        if self.incomplete is not None:
            return
        ratio, row_units = _row_ratio_and_units(row)
        if ratio is None:
            self.incomplete = ("a row has no usable AllocatedRatio", "AllocatedMethodDetails")
            return
        self.ratios.append(ratio)
        self.units |= row_units
        self.methods.add((row.get("AllocatedMethodId") or "").strip())
        resource = (row.get("AllocatedResourceId") or "").strip()
        self.resource_count += 1
        self.resources.add(resource)
        if not resource:
            self.missing_resource = True
        cost = _dec(row.get("BilledCost"))
        if cost is None:
            self.incomplete = ("a row has no numeric BilledCost", "BilledCost")
            return
        self.total_cost += cost
        origin_cost = _dec(row.get(ORIGIN_COST_COLUMN))
        if origin_cost is None:
            self.incomplete = ("missing x_SplitOriginCost", ORIGIN_COST_COLUMN)
            return
        self.origin_costs.add(origin_cost)


def validate_split_allocation(
    cost_and_usage: RowStream,
    *,
    ratio_tolerance: Decimal = DEFAULT_RATIO_TOLERANCE,
    cost_tolerance: Decimal = DEFAULT_COST_TOLERANCE,
) -> list[Diagnostic]:
    """Validate every split-cost-allocation group in ``cost_and_usage`` (single pass;
    memory is bounded by the number of allocation *groups*, not their member rows)."""
    groups: dict[str, _GroupState] = {}
    for i, row in enumerate(cost_and_usage, start=1):
        origin = (row.get(ORIGIN_ID_COLUMN) or "").strip()
        if origin:
            groups.setdefault(origin, _GroupState()).observe(i, row)

    out: list[Diagnostic] = []
    for origin_id, state in sorted(groups.items()):
        out.extend(_group_diagnostics(origin_id, state, ratio_tolerance, cost_tolerance))
    return out


def _group_diagnostics(
    origin_id: str,
    state: _GroupState,
    ratio_tolerance: Decimal,
    cost_tolerance: Decimal,
) -> list[Diagnostic]:
    keys = {ORIGIN_ID_COLUMN: origin_id}
    lines = sorted(state.lines)
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

    if state.incomplete is not None:
        return [incomplete(*state.incomplete)]
    if "" in state.methods or state.missing_resource:
        return [incomplete("a row is missing AllocatedMethodId or AllocatedResourceId")]
    if len(state.origin_costs) != 1:
        return [incomplete("rows disagree on x_SplitOriginCost", ORIGIN_COST_COLUMN)]

    for ratio in state.ratios:
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

    ratio_sum = sum(state.ratios, Decimal(0))
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

    origin_cost = next(iter(state.origin_costs))
    if abs(state.total_cost - origin_cost) > cost_tolerance:
        out.append(
            Diagnostic(
                code="FDT-ALLOC-002",
                severity=Severity.ERROR,
                message=f"allocated costs in group {origin_id!r} sum to {state.total_cost}, "
                f"not the origin cost {origin_cost}",
                datasets=("Cost and Usage",),
                dataset="Cost and Usage",
                column="BilledCost",
                expected=str(origin_cost),
                actual=str(state.total_cost),
                record_keys=keys,
            )
        )

    if len({m for m in state.methods if m}) > 1:
        out.append(
            Diagnostic(
                code="FDT-ALLOC-003",
                severity=Severity.ERROR,
                message=f"inconsistent AllocatedMethodId within group {origin_id!r}: "
                f"{sorted(state.methods)}",
                datasets=("Cost and Usage",),
                dataset="Cost and Usage",
                column="AllocatedMethodId",
                record_keys=keys,
            )
        )

    if len(state.units) > 1:
        out.append(
            Diagnostic(
                code="FDT-ALLOC-007",
                severity=Severity.ERROR,
                message=f"inconsistent UsageUnit within group {origin_id!r}: "
                f"{sorted(state.units)}",
                datasets=("Cost and Usage",),
                dataset="Cost and Usage",
                column="AllocatedMethodDetails",
                record_keys=keys,
            )
        )

    if len(state.resources) != state.resource_count:
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
