"""Cross-validate loaded supplements against the source and the FOCUS model.

Diagnostics (``FDT-SUPP-0xx``): ERRORs block use of the bundle, WARNINGs don't, and the
coverage report (``FDT-SUPP-010``, INFO) is what drives strict gating — a blocking
column flips to ``ENRICHED`` only at 100 % key coverage.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from focus_data_toolkit.convert.invoice_detail import (
    _COST_QUANTUM,
    GrainKey,
    invoice_detail_grain_key,
)
from focus_data_toolkit.errors import Diagnostic, Severity
from focus_data_toolkit.model.validator import check_column_value
from focus_data_toolkit.supplement.loader import JoinKey, SupplementBundle, SupplementTable

_SAMPLE_CAP = 25


@dataclass
class SourceKeySets:
    """The join keys the source actually derives, per supplement kind."""

    billing_periods: set[JoinKey] = field(default_factory=set)
    invoices: set[JoinKey] = field(default_factory=set)
    invoice_grains: set[GrainKey] = field(default_factory=set)
    contract_commitment_ids: set[JoinKey] = field(default_factory=set)
    grain_billed: dict[GrainKey, Decimal] = field(default_factory=dict)

    def keys_for(self, kind_name: str) -> set[JoinKey]:
        return {
            "billing_period": self.billing_periods,
            "invoice": self.invoices,
            "invoice_line": self.invoice_grains,
            "contract_commitment": self.contract_commitment_ids,
        }[kind_name]

    def observe_cau_row(self, row: Mapping[str, str]) -> None:
        """Accumulate the keys of one Cost and Usage row (shared eager/streaming)."""
        start = (row.get("BillingPeriodStart") or "").strip()
        end = (row.get("BillingPeriodEnd") or "").strip()
        issuer = (row.get("InvoiceIssuerName") or "").strip()
        if start and end:
            self.billing_periods.add((issuer, start, end))
        grain = invoice_detail_grain_key(row)
        if grain[1]:  # InvoiceId present
            self.invoice_grains.add(grain)
            self.invoices.add((grain[0], grain[1]))
            try:
                cost = Decimal((row.get("BilledCost") or "0").strip() or "0")
            except InvalidOperation:
                cost = Decimal(0)
            self.grain_billed[grain] = self.grain_billed.get(grain, Decimal(0)) + cost

    def observe_cc_row(self, row: Mapping[str, str]) -> None:
        cc_id = (row.get("ContractCommitmentId") or "").strip()
        if cc_id:
            self.contract_commitment_ids.add((cc_id,))


def source_key_sets(
    cau_rows: Sequence[Mapping[str, str]],
    cc_rows: Sequence[Mapping[str, str]] | None = None,
) -> SourceKeySets:
    keys = SourceKeySets()
    for row in cau_rows:
        keys.observe_cau_row(row)
    for row in cc_rows or ():
        keys.observe_cc_row(row)
    return keys


@dataclass(frozen=True)
class ColumnCoverage:
    """How many source keys have a non-empty supplied value for one column."""

    total_keys: int
    covered: int

    @property
    def complete(self) -> bool:
        return self.total_keys > 0 and self.covered == self.total_keys


def coverage(table: SupplementTable, source_keys: set[JoinKey]) -> dict[str, ColumnCoverage]:
    """Per fact column: how many of the source's keys this table covers."""
    total = len(source_keys)
    out: dict[str, ColumnCoverage] = {}
    for column in table.fact_columns:
        covered = sum(1 for key in source_keys if table.value(key, column))
        out[column] = ColumnCoverage(total_keys=total, covered=covered)
    return out


def _sample(keys: Sequence[JoinKey]) -> str:
    return "; ".join("|".join(k) for k in list(keys)[:_SAMPLE_CAP])


def validate_supplements(bundle: SupplementBundle, source: SourceKeySets) -> list[Diagnostic]:
    """All supplement diagnostics: structural + values + joins + coverage."""
    diagnostics = bundle.structural_diagnostics()
    for name in sorted(bundle.tables):
        table = bundle.tables[name]
        source_keys = source.keys_for(name)

        # FDT-SUPP-004 — supplied values must obey the model's format rules.
        bad: dict[str, list[str]] = {}
        for key, facts in table.rows.items():
            for column, value in facts.items():
                if not value:
                    continue
                rule = check_column_value(table.kind.target_dataset, column, value)
                if rule:
                    bad.setdefault(f"{column}:{rule}", []).append("|".join(key))
        for column_rule, keys in sorted(bad.items()):
            column, _, rule = column_rule.partition(":")
            diagnostics.append(
                Diagnostic(
                    code="FDT-SUPP-004",
                    severity=Severity.ERROR,
                    message=f"supplement value(s) for {column} violate the model rule {rule!r}",
                    datasets=(table.kind.target_dataset,),
                    file=str(table.path),
                    context={
                        "kind": name,
                        "column": column,
                        "rule": rule,
                        "row_count": str(len(keys)),
                        "sample": "; ".join(keys[:_SAMPLE_CAP]),
                    },
                )
            )

        # FDT-SUPP-005 — orphan supplement rows (key never derived from the source).
        orphans = sorted(set(table.rows) - source_keys)
        if orphans:
            diagnostics.append(
                Diagnostic(
                    code="FDT-SUPP-005",
                    severity=Severity.WARNING,
                    message=f"{len(orphans)} supplement row(s) match nothing in the source "
                    "(often a wider export period; they are ignored)",
                    datasets=(table.kind.target_dataset,),
                    file=str(table.path),
                    context={"kind": name, "sample": _sample(orphans)},
                )
            )

        # FDT-SUPP-006 — invoice_line BilledCost must reconcile with the derived sums.
        if name == "invoice_line" and "BilledCost" in table.fact_columns and source.grain_billed:
            conflicts: list[JoinKey] = []
            for key in sorted(set(table.rows) & source_keys):
                supplied = table.value(key, "BilledCost")
                if not supplied:
                    continue
                try:
                    supplied_cost = Decimal(supplied)
                except InvalidOperation:
                    continue  # already reported by FDT-SUPP-004
                derived = source.grain_billed.get(key, Decimal(0))
                if supplied_cost.quantize(_COST_QUANTUM) != derived.quantize(_COST_QUANTUM):
                    conflicts.append(key)
            if conflicts:
                diagnostics.append(
                    Diagnostic(
                        code="FDT-SUPP-006",
                        severity=Severity.ERROR,
                        message="supplement BilledCost conflicts with the Cost and Usage "
                        "grain sums; the supplement does not describe this source",
                        datasets=(table.kind.target_dataset,),
                        file=str(table.path),
                        context={
                            "kind": name,
                            "row_count": str(len(conflicts)),
                            "sample": _sample(conflicts),
                        },
                    )
                )

        # FDT-SUPP-010 — coverage per column (drives strict gating; INFO, never an error).
        for column, cov in sorted(coverage(table, source_keys).items()):
            if column == "BilledCost" and name == "invoice_line":
                continue  # reconciliation-only, never applied
            if cov.covered < cov.total_keys:
                missing = sorted(
                    k for k in source_keys if not table.value(k, column)
                )
                diagnostics.append(
                    Diagnostic(
                        code="FDT-SUPP-010",
                        severity=Severity.INFO,
                        message=f"partial coverage for {column}: "
                        f"{cov.covered}/{cov.total_keys} source key(s) supplied",
                        datasets=(table.kind.target_dataset,),
                        file=str(table.path),
                        context={
                            "kind": name,
                            "column": column,
                            "covered": str(cov.covered),
                            "total": str(cov.total_keys),
                            "missing_sample": _sample(missing),
                        },
                    )
                )
    return diagnostics


def has_blocking_errors(diagnostics: Sequence[Diagnostic]) -> bool:
    return any(d.severity is Severity.ERROR for d in diagnostics)
