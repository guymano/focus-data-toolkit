"""Referential integrity across FOCUS datasets: uniqueness, foreign keys, coherence.

These checks operate on a *bundle* of datasets (dataset name -> rows) and never live inside
the per-dataset linter — they are inherently cross-dataset. Every finding is a structured
:class:`~focus_data_toolkit.errors.Diagnostic` carrying the offending record's business key.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from focus_data_toolkit.convert.contract_applied import ContractAppliedError, parse
from focus_data_toolkit.errors import Diagnostic, Severity

Rows = Sequence[Mapping[str, str]]


def _cu_keys(row: Mapping[str, str]) -> dict[str, str]:
    """Business key for a Cost and Usage row, for diagnostics."""
    keys = {}
    for col in ("InvoiceIssuerName", "InvoiceId", "BillingAccountId", "ResourceId"):
        val = (row.get(col) or "").strip()
        if val:
            keys[col] = val
    return keys


def check_unique_invoice_detail_ids(invoice_detail: Rows) -> list[Diagnostic]:
    """InvoiceDetailId must be unique within Invoice Detail."""
    seen: dict[str, int] = {}
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
            seen[detail_id] = i
    return out


def check_cost_and_usage_invoice_detail_fk(
    cost_and_usage: Rows, invoice_detail: Rows
) -> list[Diagnostic]:
    """Every non-empty Cost and Usage InvoiceDetailId must exist in Invoice Detail."""
    known = {(r.get("InvoiceDetailId") or "").strip() for r in invoice_detail}
    known.discard("")
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
    cost_and_usage: Rows, contract_commitment: Rows
) -> list[Diagnostic]:
    """Every ContractCommitmentId referenced from ContractApplied must exist."""
    known = {(r.get("ContractCommitmentId") or "").strip() for r in contract_commitment}
    known.discard("")
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
    cost_and_usage: Rows, billing_period: Rows
) -> list[Diagnostic]:
    """Every (period, issuer) seen in Cost and Usage must have a Billing Period row."""
    known = {
        (
            (r.get("BillingPeriodStart") or "").strip(),
            (r.get("BillingPeriodEnd") or "").strip(),
            (r.get("InvoiceIssuerName") or "").strip(),
        )
        for r in billing_period
    }
    reported: set[tuple[str, str, str]] = set()
    out: list[Diagnostic] = []
    for i, row in enumerate(cost_and_usage, start=1):
        key = (
            (row.get("BillingPeriodStart") or "").strip(),
            (row.get("BillingPeriodEnd") or "").strip(),
            (row.get("InvoiceIssuerName") or "").strip(),
        )
        if not key[0] or key in known or key in reported:
            continue
        reported.add(key)
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


_CONSISTENCY_COLUMNS = {
    "BillingCurrency": "FDT-CROSS-020",
    "BillingPeriodStart": "FDT-CROSS-021",
    "BillingPeriodEnd": "FDT-CROSS-021",
    "InvoiceIssuerName": "FDT-CROSS-022",
    "BillingAccountId": "FDT-CROSS-023",
}


def check_cost_and_usage_invoice_detail_consistency(
    cost_and_usage: Rows, invoice_detail: Rows
) -> list[Diagnostic]:
    """A Cost and Usage row's issuer/account/currency/period must match its Invoice Detail line."""
    index = {
        (r.get("InvoiceDetailId") or "").strip(): r
        for r in invoice_detail
        if (r.get("InvoiceDetailId") or "").strip()
    }
    out: list[Diagnostic] = []
    for i, row in enumerate(cost_and_usage, start=1):
        ref = (row.get("InvoiceDetailId") or "").strip()
        detail = index.get(ref)
        if detail is None:
            continue  # missing FK already reported elsewhere
        for column, code in _CONSISTENCY_COLUMNS.items():
            expected = (detail.get(column) or "").strip()
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
