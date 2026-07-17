"""Apply validated supplements to the derived datasets (ENRICHED lineage).

Mode rules, per fact column present in a supplement table:

* **Strict** — a value is either supplied by the client or empty; synthetic defaults
  are never emitted. A *non-nullable* column's rule flips to ``ENRICHED`` only at
  100 % key coverage (otherwise it keeps blocking and the dataset stays
  ``NOT_PRODUCED``); a *nullable* column flips to ``ENRICHED`` as soon as the
  supplement carries it, with per-value counters recording the supplied/null mix.
* **Synthetic** — a supplied value wins, the documented synthetic default fills the
  rest. The headline rule flips to ``ENRICHED`` only at full coverage (otherwise it
  stays ``ASSUMED`` — the weakest lineage present), with counters showing the mix.

The strict gate itself is untouched: ``ENRICHED`` is already factual, so a fully
covered dataset simply stops blocking.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from focus_data_toolkit.convert.invoice_detail import (
    GrainKey,
    invoice_detail_grain_key,
)
from focus_data_toolkit.model import dataset_columns
from focus_data_toolkit.model.validator import load_model
from focus_data_toolkit.provenance import ColumnRule, Lineage, LineageCounters
from focus_data_toolkit.supplement.loader import SupplementBundle, SupplementTable
from focus_data_toolkit.supplement.validate import SourceKeySets, coverage

# Invoice Detail columns normally omitted (unfilled conditionals) that a supplement can
# activate; non-nullable ones require full coverage to be emitted at all.
_ACTIVATABLE_INVOICE_COLUMNS = (
    "PaymentCurrency",
    "PaymentCurrencyBilledCost",
    "PaymentCurrencyInvoiceDetailId",
    "PurchaseOrderNumber",
)


@dataclass
class AppliedDataset:
    """Outcome of applying supplements to one dataset."""

    rows: list[dict[str, str]] | None
    provenance: dict[str, ColumnRule]
    counters: LineageCounters = field(default_factory=LineageCounters)


def _source_label(table: SupplementTable, column: str) -> str:
    # Per-column attribution: a merged table (adapter export + hand-authored file) tags each
    # column to its originating file ("supplement:<adapter>@<version>:<file>" or the kind).
    return table.source_for(column)


def _allows_nulls(dataset: str, column: str) -> bool:
    spec = load_model()["datasets"][dataset]["columns"].get(column) or {}
    return bool(spec.get("allows_nulls", True))


def _flip_rules(
    base: dict[str, ColumnRule],
    dataset: str,
    tables: list[SupplementTable],
    source: SourceKeySets,
) -> dict[str, ColumnRule]:
    """Return ``base`` with columns flipped to ENRICHED per the coverage rules."""
    rules = dict(base)
    for table in tables:
        cov = coverage(table, source.keys_for(table.kind.name))
        for column in table.fact_columns:
            if table.kind.name == "invoice_line" and column == "BilledCost":
                continue  # reconciliation-only, never applied
            if column not in dataset_columns(dataset):
                continue
            col_cov = cov[column]
            complete = col_cov.complete
            nullable = _allows_nulls(dataset, column)
            if complete or (nullable and col_cov.covered > 0):
                note = None if complete else "nulls where the client supplied no value"
                rules[column] = ColumnRule(Lineage.ENRICHED, _source_label(table, column), note)
    return rules


# Public alias: the streaming pipeline pre-computes the rule flips (rows-independent)
# to decide strict back-link gating before the main pass.
flip_enriched_rules = _flip_rules


def _apply_column(
    row: dict[str, str],
    column: str,
    supplied: str,
    *,
    synthetic: bool,
    counters: LineageCounters,
    base_is_factual: bool = False,
) -> None:
    """Write one fact column: supplied value, else keep the base value / synthetic default.

    ``base_is_factual`` marks an *override* column that already carries a factual base value
    (e.g. Contract Commitment ``ServiceProviderName`` from the provider context). For an
    uncovered row such a column must keep its base value — never be blanked — so a partial
    override does not turn otherwise-valid rows into mandatory-null failures.
    """
    if supplied:
        row[column] = supplied
        counters.record(column, Lineage.ENRICHED)
    elif base_is_factual and (row.get(column) or ""):
        # Keep the factual base value for this uncovered row.
        counters.record(column, Lineage.ENRICHED)
    elif synthetic:
        # Keep the builder's documented default (assumed) — or null if it emitted none.
        counters.record(
            column, Lineage.ASSUMED if (row.get(column) or "") else Lineage.UNAVAILABLE
        )
    else:
        row[column] = ""
        counters.record(column, Lineage.UNAVAILABLE)


def _strict_suppress_uncovered_assumed(
    applied: AppliedDataset,
    dataset: str,
    tables: list[SupplementTable],
    *,
    synthetic: bool,
) -> None:
    """In strict mode, blank uncovered nullable ASSUMED columns (never emit a default).

    Uncovered non-nullable ASSUMED columns keep blocking (the dataset stays
    ``NOT_PRODUCED``); nullable ones must not leak their synthetic default into a
    strictly-produced dataset, so they are emptied with ``UNAVAILABLE`` lineage.
    """
    if synthetic:
        return
    covered = {c for t in tables for c in t.fact_columns}
    for column, rule in list(applied.provenance.items()):
        if rule.lineage is not Lineage.ASSUMED or column in covered:
            continue
        if not _allows_nulls(dataset, column):
            continue
        for row in applied.rows or []:
            row[column] = ""
        applied.provenance[column] = ColumnRule(
            Lineage.UNAVAILABLE, note="synthetic default suppressed in strict mode"
        )


def apply_billing_periods(
    rows: list[dict[str, str]],
    bundle: SupplementBundle,
    source: SourceKeySets,
    base_provenance: dict[str, ColumnRule],
    *,
    synthetic: bool,
) -> AppliedDataset:
    table = bundle.get("billing_period")
    if table is None:
        return AppliedDataset(rows=rows, provenance=dict(base_provenance))
    out = AppliedDataset(
        rows=[dict(r) for r in rows],
        provenance=_flip_rules(base_provenance, "Billing Period", [table], source),
    )
    for row in out.rows or []:
        key = (
            row.get("InvoiceIssuerName", ""),
            row.get("BillingPeriodStart", ""),
            row.get("BillingPeriodEnd", ""),
        )
        for column in table.fact_columns:
            _apply_column(
                row, column, table.value(key, column),
                synthetic=synthetic, counters=out.counters,
            )
    _strict_suppress_uncovered_assumed(out, "Billing Period", [table], synthetic=synthetic)
    return out


def apply_invoice_details(
    rows: list[dict[str, str]],
    id_mapping: dict[GrainKey, str],
    bundle: SupplementBundle,
    source: SourceKeySets,
    base_provenance: dict[str, ColumnRule],
    *,
    synthetic: bool,
) -> tuple[AppliedDataset, dict[GrainKey, str]]:
    """Apply invoice-header and invoice-line supplements; returns the updated id mapping."""
    invoice = bundle.get("invoice")
    line = bundle.get("invoice_line")
    if invoice is None and line is None:
        return AppliedDataset(rows=rows, provenance=dict(base_provenance)), id_mapping
    tables = [t for t in (invoice, line) if t is not None]
    out = AppliedDataset(
        rows=[],
        provenance=_flip_rules(base_provenance, "Invoice Detail", tables, source),
    )

    # Conditional columns activate only when the supplement genuinely enables them.
    extra_emitted: list[str] = []
    for column in _ACTIVATABLE_INVOICE_COLUMNS:
        for table in tables:
            if column not in table.fact_columns:
                continue
            col_cov = coverage(table, source.keys_for(table.kind.name))[column]
            if col_cov.complete or (_allows_nulls("Invoice Detail", column) and col_cov.covered):
                extra_emitted.append(column)
                out.provenance[column] = ColumnRule(
                    Lineage.ENRICHED,
                    _source_label(table, column),
                    None if col_cov.complete else "nulls where the client supplied no value",
                )

    emitted = [c for c in dataset_columns("Invoice Detail") if rows and c in rows[0]]
    all_emitted = [
        c for c in dataset_columns("Invoice Detail") if c in set(emitted) | set(extra_emitted)
    ]

    new_mapping = dict(id_mapping)
    for row in rows:
        merged = {c: row.get(c, "") for c in all_emitted}
        grain = invoice_detail_grain_key(row)
        if invoice is not None:
            header_key = (row.get("InvoiceIssuerName", ""), row.get("InvoiceId", ""))
            for column in invoice.fact_columns:
                if column not in all_emitted:
                    continue
                _apply_column(
                    merged, column, invoice.value(header_key, column),
                    synthetic=synthetic, counters=out.counters,
                )
        if line is not None:
            for column in line.fact_columns:
                if column == "BilledCost" or column not in all_emitted:
                    continue
                _apply_column(
                    merged, column, line.value(grain, column),
                    synthetic=synthetic, counters=out.counters,
                )
            real_id = line.value(grain, "InvoiceDetailId")
            if real_id:
                new_mapping[grain] = real_id
        assert out.rows is not None
        out.rows.append(merged)
    _strict_suppress_uncovered_assumed(out, "Invoice Detail", tables, synthetic=synthetic)
    return out, new_mapping


def apply_contract_commitments(
    rows: list[dict[str, str]],
    bundle: SupplementBundle,
    source: SourceKeySets,
    base_provenance: dict[str, ColumnRule],
    *,
    synthetic: bool,
) -> AppliedDataset:
    table = bundle.get("contract_commitment")
    if table is None:
        return AppliedDataset(rows=rows, provenance=dict(base_provenance))
    out = AppliedDataset(
        rows=[dict(r) for r in rows],
        provenance=_flip_rules(base_provenance, "Contract Commitment", [table], source),
    )
    upfront = "ContractCommitmentPaymentUpfrontPercentage"
    model_col = "ContractCommitmentPaymentModel"
    upfront_supplied = upfront in table.fact_columns
    model_cov = (
        coverage(table, source.keys_for("contract_commitment")).get(model_col)
        if model_col in table.fact_columns
        else None
    )
    # Override columns whose base row already holds a factual (provider-context) value:
    # a partial override must not blank the uncovered rows.
    factual_base = {c for c, r in base_provenance.items() if r.is_factual}
    for row in out.rows or []:
        key = (row.get("ContractCommitmentId", ""),)
        for column in table.fact_columns:
            if column == upfront:
                continue  # handled below (derivable from the payment model)
            _apply_column(
                row, column, table.value(key, column),
                synthetic=synthetic, counters=out.counters,
                base_is_factual=column in factual_base,
            )
        # Upfront percentage: supplied wins; else exactly derivable from the payment
        # model ('No Upfront' -> 0, 'All Upfront' -> 1); 'Partial Upfront' without a
        # supplied percentage is not derivable and stays empty in BOTH modes (never a
        # guessed '0' paired with 'Partial Upfront'); the mandatory-column lint flags it.
        supplied_pct = table.value(key, upfront) if upfront_supplied else ""
        payment_model = row.get(model_col, "")
        if supplied_pct:
            row[upfront] = supplied_pct
            out.counters.record(upfront, Lineage.ENRICHED)
        elif payment_model == "No Upfront":
            row[upfront] = "0"
            out.counters.record(upfront, Lineage.DERIVED)
        elif payment_model == "All Upfront":
            row[upfront] = "1"
            out.counters.record(upfront, Lineage.DERIVED)
        else:
            row[upfront] = ""
            out.counters.record(upfront, Lineage.UNAVAILABLE)
    if not any(
        (r.get(upfront) or "") == "" for r in (out.rows or [])
    ) and (upfront_supplied or (model_cov is not None and model_cov.complete)):
        source_note = (
            _source_label(table, upfront)
            if upfront_supplied
            else f"ContractCommitmentPaymentModel ({_source_label(table, model_col)})"
        )
        lineage = Lineage.ENRICHED if upfront_supplied else Lineage.DERIVED
        out.provenance[upfront] = ColumnRule(lineage, source_note)
    _strict_suppress_uncovered_assumed(
        out, "Contract Commitment", [table], synthetic=synthetic
    )
    return out
