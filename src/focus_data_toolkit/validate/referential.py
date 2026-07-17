"""Referential integrity across FOCUS datasets: uniqueness, foreign keys, coherence.

These checks operate on a *bundle* of datasets (dataset name -> rows) and never live inside
the per-dataset linter — they are inherently cross-dataset. Every finding is a structured
:class:`~focus_data_toolkit.errors.Diagnostic` carrying the offending record's business key.

Every check consumes each input in a single forward pass and keeps only per-key lookup
state; ``index_factory`` lets the caller back that state with a disk-spilling map
(:class:`~focus_data_toolkit.storage.spill.SpillableMap`) so validating datasets far larger
than RAM stays memory-bounded.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence

from focus_data_toolkit.convert.contract_applied import ContractAppliedError, parse
from focus_data_toolkit.errors import Diagnostic, Severity

Rows = Sequence[Mapping[str, str]]
#: Every dataset side of a cross-dataset check is consumed in a single forward pass, so it
#: accepts any iterable of rows (e.g. a staged-file stream) — bounded memory.
RowStream = Iterable[Mapping[str, str]]
#: Factory for the per-key lookup state (``dict`` by default; a spillable map for streaming).
IndexFactory = Callable[[], MutableMapping[str, str]]


def _composite(*parts: str) -> str:
    """Unambiguous single-string key for a tuple of values (JSON array encoding)."""
    return json.dumps(parts, separators=(",", ":"))


def _cu_keys(row: Mapping[str, str]) -> dict[str, str]:
    """Business key for a Cost and Usage row, for diagnostics."""
    keys = {}
    for col in ("InvoiceIssuerName", "InvoiceId", "BillingAccountId", "ResourceId"):
        val = (row.get(col) or "").strip()
        if val:
            keys[col] = val
    return keys


def check_unique_invoice_detail_ids(
    invoice_detail: RowStream, *, index_factory: IndexFactory = dict
) -> list[Diagnostic]:
    """InvoiceDetailId must be unique within Invoice Detail."""
    seen = index_factory()
    out: list[Diagnostic] = []
    for i, row in enumerate(invoice_detail, start=1):
        detail_id = (row.get("InvoiceDetailId") or "").strip()
        if not detail_id:
            continue
        if detail_id in seen:
            out.append(
                Diagnostic(
                    code="FDT-CROSS-001",
                    severity=Severity.ERROR,
                    message=f"InvoiceDetailId {detail_id!r} is not unique in Invoice Detail "
                    f"(rows {seen[detail_id]} and {i})",
                    datasets=("Invoice Detail",),
                    dataset="Invoice Detail",
                    line_number=i,
                    column="InvoiceDetailId",
                    value=detail_id,
                    record_keys={"InvoiceDetailId": detail_id},
                )
            )
        else:
            seen[detail_id] = str(i)
    return out


def check_unique_contract_commitment_ids(
    contract_commitment: RowStream, *, index_factory: IndexFactory = dict
) -> list[Diagnostic]:
    """ContractCommitmentId must be unique (ContractApplied resolves commitments by this id)."""
    seen = index_factory()
    out: list[Diagnostic] = []
    for i, row in enumerate(contract_commitment, start=1):
        commitment_id = (row.get("ContractCommitmentId") or "").strip()
        if not commitment_id:
            continue
        if commitment_id in seen:
            out.append(
                Diagnostic(
                    code="FDT-CROSS-001",
                    severity=Severity.ERROR,
                    message=f"ContractCommitmentId {commitment_id!r} is not unique in Contract "
                    f"Commitment (rows {seen[commitment_id]} and {i})",
                    datasets=("Contract Commitment",),
                    dataset="Contract Commitment",
                    line_number=i,
                    column="ContractCommitmentId",
                    value=commitment_id,
                    record_keys={"ContractCommitmentId": commitment_id},
                )
            )
        else:
            seen[commitment_id] = str(i)
    return out


def check_cost_and_usage_invoice_detail_fk(
    cost_and_usage: RowStream,
    invoice_detail: RowStream,
    *,
    index_factory: IndexFactory = dict,
) -> list[Diagnostic]:
    """Every non-empty Cost and Usage InvoiceDetailId must exist in Invoice Detail."""
    known = index_factory()
    for r in invoice_detail:
        detail_id = (r.get("InvoiceDetailId") or "").strip()
        if detail_id:
            known[detail_id] = ""
    out: list[Diagnostic] = []
    for i, row in enumerate(cost_and_usage, start=1):
        ref = (row.get("InvoiceDetailId") or "").strip()
        if not ref or ref in known:
            continue
        out.append(
            Diagnostic(
                code="FDT-CROSS-014",
                severity=Severity.ERROR,
                message=f"InvoiceDetailId {ref!r} referenced by Cost and Usage row {i} was not "
                "found in Invoice Detail",
                datasets=("Cost and Usage", "Invoice Detail"),
                dataset="Cost and Usage",
                line_number=i,
                column="InvoiceDetailId",
                value=ref,
                record_keys=_cu_keys(row),
                source="Cost and Usage",
            )
        )
    return out


def _commitment_ids(cost_and_usage_row: Mapping[str, str]) -> list[str]:
    """ContractCommitmentIds referenced by a row's ContractApplied JSON (best effort)."""
    text = (cost_and_usage_row.get("ContractApplied") or "").strip()
    if not text:
        return []
    try:
        applied = parse(text, version="1.4")
    except ContractAppliedError:
        return []  # structural validity is the per-dataset linter's job
    return [e.contract_commitment_id for e in applied.elements if e.contract_commitment_id]


def check_contract_applied_fk(
    cost_and_usage: RowStream,
    contract_commitment: RowStream,
    *,
    index_factory: IndexFactory = dict,
) -> list[Diagnostic]:
    """Every ContractCommitmentId referenced from ContractApplied must exist."""
    known = index_factory()
    for r in contract_commitment:
        commitment_id = (r.get("ContractCommitmentId") or "").strip()
        if commitment_id:
            known[commitment_id] = ""
    out: list[Diagnostic] = []
    for i, row in enumerate(cost_and_usage, start=1):
        for commitment_id in _commitment_ids(row):
            if commitment_id in known:
                continue
            out.append(
                Diagnostic(
                    code="FDT-CROSS-010",
                    severity=Severity.ERROR,
                    message=f"ContractApplied on Cost and Usage row {i} references "
                    f"ContractCommitmentId {commitment_id!r} not found in Contract Commitment",
                    datasets=("Cost and Usage", "Contract Commitment"),
                    dataset="Cost and Usage",
                    line_number=i,
                    column="ContractApplied",
                    value=commitment_id,
                    record_keys=_cu_keys(row),
                )
            )
    return out


def check_billing_period_coverage(
    cost_and_usage: RowStream,
    billing_period: RowStream,
    *,
    index_factory: IndexFactory = dict,
) -> list[Diagnostic]:
    """Every (period, issuer) seen in Cost and Usage must have a Billing Period row."""
    known = index_factory()
    for r in billing_period:
        known[
            _composite(
                (r.get("BillingPeriodStart") or "").strip(),
                (r.get("BillingPeriodEnd") or "").strip(),
                (r.get("InvoiceIssuerName") or "").strip(),
            )
        ] = ""
    reported = index_factory()
    out: list[Diagnostic] = []
    for i, row in enumerate(cost_and_usage, start=1):
        key = (
            (row.get("BillingPeriodStart") or "").strip(),
            (row.get("BillingPeriodEnd") or "").strip(),
            (row.get("InvoiceIssuerName") or "").strip(),
        )
        composite = _composite(*key)
        if not key[0] or composite in known or composite in reported:
            continue
        reported[composite] = ""
        out.append(
            Diagnostic(
                code="FDT-CROSS-040",
                severity=Severity.ERROR,
                message="no Billing Period row for a (period, issuer) present in Cost and Usage",
                datasets=("Cost and Usage", "Billing Period"),
                dataset="Cost and Usage",
                line_number=i,
                record_keys={
                    "BillingPeriodStart": key[0],
                    "BillingPeriodEnd": key[1],
                    "InvoiceIssuerName": key[2],
                },
            )
        )
    return out


# Attributes that must agree between a Cost and Usage row and the Invoice Detail line it
# links to. InvoiceId / ChargeCategory identify *which* invoice line the row belongs to: a
# same-amount line under a different invoice must not be silently accepted.
_CONSISTENCY_COLUMNS = {
    "InvoiceId": "FDT-CROSS-015",
    "ChargeCategory": "FDT-CROSS-015",
    "BillingCurrency": "FDT-CROSS-020",
    "BillingPeriodStart": "FDT-CROSS-021",
    "BillingPeriodEnd": "FDT-CROSS-021",
    "InvoiceIssuerName": "FDT-CROSS-022",
    "BillingAccountId": "FDT-CROSS-023",
}


def check_cost_and_usage_invoice_detail_consistency(
    cost_and_usage: RowStream,
    invoice_detail: RowStream,
    *,
    index_factory: IndexFactory = dict,
) -> list[Diagnostic]:
    """A Cost and Usage row's invoice/category/issuer/account/currency/period must match its
    Invoice Detail line (so a row cannot be attached to the wrong invoice line)."""
    # Only the compared columns are indexed (id -> JSON array of their stripped values), so
    # the lookup stays small per line and spills cleanly through a string map.
    index = index_factory()
    for r in invoice_detail:
        detail_id = (r.get("InvoiceDetailId") or "").strip()
        if detail_id:
            index[detail_id] = _composite(
                *((r.get(c) or "").strip() for c in _CONSISTENCY_COLUMNS)
            )
    out: list[Diagnostic] = []
    for i, row in enumerate(cost_and_usage, start=1):
        ref = (row.get("InvoiceDetailId") or "").strip()
        packed = index.get(ref) if ref else None
        if packed is None:
            continue  # missing FK already reported elsewhere
        expected_values = json.loads(packed)
        for (column, code), expected in zip(
            _CONSISTENCY_COLUMNS.items(), expected_values, strict=True
        ):
            actual = (row.get(column) or "").strip()
            if expected != actual:
                out.append(
                    Diagnostic(
                        code=code,
                        severity=Severity.ERROR,
                        message=f"{column} differs between Cost and Usage row {i} and its "
                        f"Invoice Detail line {ref!r}",
                        datasets=("Cost and Usage", "Invoice Detail"),
                        dataset="Cost and Usage",
                        line_number=i,
                        column=column,
                        expected=expected,
                        actual=actual,
                        record_keys={"InvoiceDetailId": ref, **_cu_keys(row)},
                    )
                )
    return out
