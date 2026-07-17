"""Gap analysis: exactly which facts a client must supply, per FOCUS 1.4 dataset.

The gap set is *computed*, never hardcoded: a column gap is a column that is Mandatory,
non-nullable and non-factual under the very provenance rules the converter would use for
this source — i.e. exactly ``strict_blockers()``. Each gap is annotated from the embedded
model (allowed values, format, condition text) and mapped to the supplement kind(s) able
to satisfy it. Nullable non-factual columns of the same kinds are reported as
*recommended* (they never block strict production, but supplying them makes the output
more complete). The JSON output doubles as a fill-in template: it carries a ready-to-use
CSV header line per supplement kind.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from focus_data_toolkit.convert.billing_period import PROVENANCE as BILLING_PERIOD_PROVENANCE
from focus_data_toolkit.convert.contract_commitment import (
    PROVENANCE as CONTRACT_COMMITMENT_PROVENANCE,
)
from focus_data_toolkit.convert.cost_and_usage import cost_and_usage_provenance
from focus_data_toolkit.convert.invoice_detail import PROVENANCE as INVOICE_DETAIL_PROVENANCE
from focus_data_toolkit.model import FOCUS_1_4_DATASETS
from focus_data_toolkit.model.validator import load_model
from focus_data_toolkit.provenance import ColumnRule, Lineage, strict_blockers
from focus_data_toolkit.supplement.kinds import SUPPLEMENT_KINDS, kinds_for_column

GAP_REPORT_FORMAT = "1"


@dataclass(frozen=True)
class ColumnGap:
    """One column the source cannot factually populate."""

    dataset: str
    column: str
    feature_level: str
    allows_nulls: bool
    current_lineage: str
    current_note: str | None
    blocking: bool
    supplement_kinds: tuple[str, ...]
    join_keys: tuple[str, ...]
    value_format: str | None
    allowed_values: tuple[str, ...]
    condition: str | None

    def as_dict(self) -> dict:
        out: dict = {
            "dataset": self.dataset,
            "column": self.column,
            "feature_level": self.feature_level,
            "allows_nulls": self.allows_nulls,
            "current_lineage": self.current_lineage,
            "blocking": self.blocking,
            "supplement_kinds": list(self.supplement_kinds),
            "join_keys": list(self.join_keys),
        }
        if self.current_note:
            out["current_note"] = self.current_note
        if self.value_format:
            out["value_format"] = self.value_format
        if self.allowed_values:
            out["allowed_values"] = list(self.allowed_values)
        if self.condition:
            out["condition"] = self.condition
        return out


@dataclass(frozen=True)
class GapReport:
    """Everything a client needs to complete this source into factual 1.4 datasets."""

    source_version: str
    gaps: dict[str, tuple[ColumnGap, ...]] = field(default_factory=dict)
    dataset_level_gaps: dict[str, str] = field(default_factory=dict)

    def blocking(self, dataset: str) -> tuple[ColumnGap, ...]:
        return tuple(g for g in self.gaps.get(dataset, ()) if g.blocking)

    def as_dict(self) -> dict:
        """Deterministic JSON payload; doubles as a fill-in template."""
        kinds_used = sorted(
            {k for gaps in self.gaps.values() for g in gaps for k in g.supplement_kinds}
        )
        return {
            "gap_report_format": GAP_REPORT_FORMAT,
            "source_version": self.source_version,
            "datasets": {
                name: {
                    "column_gaps": [g.as_dict() for g in self.gaps.get(name, ())],
                    "strictly_producible_as_is": not self.blocking(name)
                    and name not in self.dataset_level_gaps,
                    **(
                        {"dataset_gap": self.dataset_level_gaps[name]}
                        if name in self.dataset_level_gaps
                        else {}
                    ),
                }
                for name in FOCUS_1_4_DATASETS
            },
            "supplement_templates": {
                name: {
                    "target_dataset": SUPPLEMENT_KINDS[name].target_dataset,
                    "join_keys": list(SUPPLEMENT_KINDS[name].join_keys),
                    "csv_header": ",".join(SUPPLEMENT_KINDS[name].header_template),
                }
                for name in kinds_used
            },
        }

    def render_text(self) -> str:
        lines = [f"FOCUS {self.source_version} source -> 1.4 gap report", ""]
        for name in FOCUS_1_4_DATASETS:
            gaps = self.gaps.get(name, ())
            if name in self.dataset_level_gaps:
                lines.append(f"[{name}] NOT PRODUCIBLE: {self.dataset_level_gaps[name]}")
                lines.append("")
                continue
            blocking = [g for g in gaps if g.blocking]
            recommended = [g for g in gaps if not g.blocking]
            if not blocking:
                lines.append(f"[{name}] strictly producible from this source as-is")
            else:
                lines.append(f"[{name}] blocked by {len(blocking)} column(s):")
                for g in blocking:
                    extra = f" (allowed: {', '.join(g.allowed_values)})" if g.allowed_values else ""
                    kinds = ", ".join(g.supplement_kinds) or "-"
                    lines.append(f"  - {g.column}{extra}  <- supplement kind: {kinds}")
            for g in recommended:
                lines.append(f"  ~ {g.column} (recommended, nullable)")
            lines.append("")
        kinds_used = sorted(
            {k for gaps in self.gaps.values() for g in gaps for k in g.supplement_kinds}
        )
        if kinds_used:
            lines.append("Supplement templates (CSV headers, ready to fill):")
            for name in kinds_used:
                lines.append(f"  {name}: {','.join(SUPPLEMENT_KINDS[name].header_template)}")
        return "\n".join(lines) + "\n"


def _gap(dataset: str, column: str, spec: dict, rule: ColumnRule | None, blocking: bool) -> ColumnGap:
    kinds = kinds_for_column(dataset, column)
    return ColumnGap(
        dataset=dataset,
        column=column,
        feature_level=spec.get("feature_level", ""),
        allows_nulls=bool(spec.get("allows_nulls", True)),
        current_lineage=rule.lineage.value if rule else "UNAVAILABLE",
        current_note=(rule.note if rule else None),
        blocking=blocking,
        supplement_kinds=tuple(k.name for k in kinds),
        join_keys=kinds[0].join_keys if kinds else (),
        value_format=spec.get("value_format") or spec.get("data_type"),
        allowed_values=tuple(spec.get("allowed_values") or ()),
        condition=spec.get("condition"),
    )


def compute_gaps(
    source_columns: Iterable[str],
    source_version: str,
    *,
    cc_columns: Iterable[str] | None = None,
) -> GapReport:
    """Compute the gap report for a Cost and Usage header (and optional 1.3 CC header).

    Uses the same provenance rules as the converter, so a reported blocking gap is by
    construction exactly what would block strict production of that dataset.
    """
    model = load_model()
    # When a Contract Commitment source header is given, reflect it: a column the base
    # provenance treats as OBSERVED-from-1.3 but that is absent from the actual header is a
    # source-completeness gap (no supplement can fabricate it), not an observed value.
    cc_prov = dict(CONTRACT_COMMITMENT_PROVENANCE)
    if cc_columns is not None:
        present_cc = set(cc_columns)
        for col, base_rule in CONTRACT_COMMITMENT_PROVENANCE.items():
            if base_rule.lineage is Lineage.OBSERVED and col not in present_cc:
                cc_prov[col] = ColumnRule(
                    Lineage.UNAVAILABLE, note="absent from the Contract Commitment source"
                )
    provenance: dict[str, dict[str, ColumnRule]] = {
        "Cost and Usage": cost_and_usage_provenance(
            source_columns, source_version, invoice_detail_linked=False
        ),
        "Billing Period": BILLING_PERIOD_PROVENANCE,
        "Invoice Detail": INVOICE_DETAIL_PROVENANCE,
        "Contract Commitment": cc_prov,
    }
    gaps: dict[str, tuple[ColumnGap, ...]] = {}
    dataset_level: dict[str, str] = {}
    for name in FOCUS_1_4_DATASETS:
        columns: dict = model["datasets"][name]["columns"]
        prov = provenance[name]
        blockers = set(strict_blockers(prov, columns))
        out: list[ColumnGap] = []
        for col in sorted(columns):
            spec = columns[col]
            rule = prov.get(col)
            if col in blockers:
                out.append(_gap(name, col, spec, rule, blocking=True))
            elif (
                rule is not None
                and not rule.is_factual
                and spec.get("allows_nulls", True)
                and kinds_for_column(name, col)
            ):
                # Nullable, non-factual, and a supplement kind can supply it: recommended.
                out.append(_gap(name, col, spec, rule, blocking=False))
        gaps[name] = tuple(out)
    if cc_columns is None:
        dataset_level["Contract Commitment"] = (
            "no FOCUS 1.3 Contract Commitment source provided; supply the 1.3 dataset "
            "(13 columns) plus a 'contract_commitment' supplement for the 1.4-new terms"
        )
    return GapReport(source_version=source_version, gaps=gaps, dataset_level_gaps=dataset_level)
