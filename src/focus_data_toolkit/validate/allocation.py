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
from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from focus_data_toolkit.errors import Diagnostic, Severity

Rows = Sequence[Mapping[str, str]]
#: The input is consumed in a single forward pass, so any iterable of rows works.
RowStream = Iterable[Mapping[str, str]]
#: Factory for per-key lookup state (``dict`` by default; a spillable map for streaming).
IndexFactory = Callable[[], MutableMapping[str, str]]

ORIGIN_ID_COLUMN = "x_SplitOriginId"
ORIGIN_COST_COLUMN = "x_SplitOriginCost"

DEFAULT_RATIO_TOLERANCE = Decimal("0.0001")
DEFAULT_COST_TOLERANCE = Decimal("0.01")

# Line numbers recorded per group for the incomplete-group diagnostic's ``rows`` context;
# beyond this the context reports the overflow as ``+N more`` instead of growing unboundedly.
_LINE_SAMPLE = 100


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
    """Running aggregate of one allocation group — fixed-size scalars, never member rows.

    Mirrors the checks of the original list-of-members implementation exactly: the first
    incomplete condition (in row order) short-circuits further accumulation, line numbers
    keep counting so the incomplete diagnostic still describes every member row. The state
    is JSON round-trippable (:meth:`dump` / :meth:`load`), so it can live as a string value
    in a disk-spilling map when the caller supplies an ``index_factory``.
    """

    lines: list[int] = field(default_factory=list)  # first _LINE_SAMPLE member lines
    line_count: int = 0
    incomplete: tuple[str, str | None] | None = None  # (reason, column)
    ratio_sum: Decimal = Decimal(0)
    out_of_range: list[Decimal] = field(default_factory=list)  # ratios outside [0, 1]
    units: set[str] = field(default_factory=set)
    methods: set[str] = field(default_factory=set)
    resource_count: int = 0
    duplicate_resource: bool = False
    missing_resource: bool = False
    total_cost: Decimal = Decimal(0)
    origin_cost: Decimal | None = None
    origin_disagrees: bool = False

    def observe(self, line: int, row: Mapping[str, str], *, seen_resource: bool) -> None:
        self.line_count += 1
        if len(self.lines) < _LINE_SAMPLE:
            self.lines.append(line)
        if self.incomplete is not None:
            return
        ratio, row_units = _row_ratio_and_units(row)
        if ratio is None:
            self.incomplete = ("a row has no usable AllocatedRatio", "AllocatedMethodDetails")
            return
        self.ratio_sum += ratio
        if ratio < 0 or ratio > 1:
            self.out_of_range.append(ratio)
        self.units |= row_units
        self.methods.add((row.get("AllocatedMethodId") or "").strip())
        resource = (row.get("AllocatedResourceId") or "").strip()
        self.resource_count += 1
        if seen_resource:
            self.duplicate_resource = True
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
        if self.origin_cost is None:
            self.origin_cost = origin_cost
        elif origin_cost != self.origin_cost:
            self.origin_disagrees = True

    def dump(self) -> str:
        return json.dumps({
            "ln": self.lines,
            "lc": self.line_count,
            "inc": list(self.incomplete) if self.incomplete else None,
            "rs": str(self.ratio_sum),
            "oor": [str(r) for r in self.out_of_range],
            "un": sorted(self.units),
            "me": sorted(self.methods),
            "rc": self.resource_count,
            "dup": self.duplicate_resource,
            "mr": self.missing_resource,
            "tc": str(self.total_cost),
            "oc": str(self.origin_cost) if self.origin_cost is not None else None,
            "od": self.origin_disagrees,
        }, separators=(",", ":"))

    @classmethod
    def load(cls, text: str) -> _GroupState:
        d = json.loads(text)
        return cls(
            lines=d["ln"],
            line_count=d["lc"],
            incomplete=tuple(d["inc"]) if d["inc"] else None,
            ratio_sum=Decimal(d["rs"]),
            out_of_range=[Decimal(r) for r in d["oor"]],
            units=set(d["un"]),
            methods=set(d["me"]),
            resource_count=d["rc"],
            duplicate_resource=d["dup"],
            missing_resource=d["mr"],
            total_cost=Decimal(d["tc"]),
            origin_cost=Decimal(d["oc"]) if d["oc"] is not None else None,
            origin_disagrees=d["od"],
        )


def validate_split_allocation(
    cost_and_usage: RowStream,
    *,
    ratio_tolerance: Decimal = DEFAULT_RATIO_TOLERANCE,
    cost_tolerance: Decimal = DEFAULT_COST_TOLERANCE,
    index_factory: IndexFactory = dict,
) -> list[Diagnostic]:
    """Validate every split-cost-allocation group in ``cost_and_usage`` (single pass).

    Per-group state is a fixed-size JSON-serialised aggregate held in an ``index_factory``
    map (as is the resource-duplicate lookup), so with a spillable factory memory stays
    bounded even when allocation-group cardinality approaches the row count.
    """
    groups = index_factory()
    resources_seen = index_factory()
    for i, row in enumerate(cost_and_usage, start=1):
        origin = (row.get(ORIGIN_ID_COLUMN) or "").strip()
        if not origin:
            continue
        raw = groups.get(origin)
        state = _GroupState.load(raw) if raw is not None else _GroupState()
        resource = (row.get("AllocatedResourceId") or "").strip()
        resource_key = json.dumps([origin, resource], separators=(",", ":"))
        state.observe(i, row, seen_resource=resource_key in resources_seen)
        if state.incomplete is None:
            resources_seen[resource_key] = ""
        groups[origin] = state.dump()

    out: list[Diagnostic] = []
    for origin_id in sorted(groups):
        state = _GroupState.load(groups[origin_id])
        out.extend(_group_diagnostics(origin_id, state, ratio_tolerance, cost_tolerance))
    return out


def _group_diagnostics(
    origin_id: str,
    state: _GroupState,
    ratio_tolerance: Decimal,
    cost_tolerance: Decimal,
) -> list[Diagnostic]:
    keys = {ORIGIN_ID_COLUMN: origin_id}
    rows_context = ",".join(map(str, sorted(state.lines)))
    if state.line_count > len(state.lines):
        rows_context += f",+{state.line_count - len(state.lines)} more"
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
            context={"rows": rows_context},
        )

    if state.incomplete is not None:
        return [incomplete(*state.incomplete)]
    if "" in state.methods or state.missing_resource:
        return [incomplete("a row is missing AllocatedMethodId or AllocatedResourceId")]
    if state.origin_disagrees:
        return [incomplete("rows disagree on x_SplitOriginCost", ORIGIN_COST_COLUMN)]

    for ratio in state.out_of_range:
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

    ratio_sum = state.ratio_sum
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

    origin_cost = state.origin_cost
    assert origin_cost is not None  # a complete group recorded one on every row
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

    if state.duplicate_resource:
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
