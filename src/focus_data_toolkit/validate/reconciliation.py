"""Reconcile Cost and Usage against an authoritative Invoice Detail dataset.

FOCUS 1.4 explicitly allows an issued invoice to differ from summed usage (it adds an
*Invoice Reconciliation* feature and a *Rounding Variance Tolerance* appendix). So this check
runs only when Invoice Detail comes from an **authoritative** source, and compares sums with an
explicit, documented tolerance rather than requiring exact equality.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation

from focus_data_toolkit.errors import Diagnostic, Severity

Rows = Sequence[Mapping[str, str]]

# Default rounding-variance tolerance for a reconciled sum (absolute, in billing currency).
DEFAULT_TOLERANCE = Decimal("0.01")


def _dec(value: str | None) -> Decimal:
    try:
        return Decimal((value or "0").strip() or "0")
    except InvalidOperation:
        return Decimal(0)


def reconcile_invoice_detail(
    cost_and_usage: Rows,
    invoice_detail: Rows,
    *,
    tolerance: Decimal = DEFAULT_TOLERANCE,
) -> list[Diagnostic]:
    """Compare each Invoice Detail BilledCost to the sum of its Cost and Usage lines.

    Cost and Usage rows are attributed to an invoice line by their ``InvoiceDetailId``
    back-link. A line with no matching Cost and Usage rows is a warning; a sum that differs
    beyond ``tolerance`` is an error carrying the expected/actual amounts.
    """
    sums: dict[str, Decimal] = {}
    for row in cost_and_usage:
        ref = (row.get("InvoiceDetailId") or "").strip()
        if ref:
            sums[ref] = sums.get(ref, Decimal(0)) + _dec(row.get("BilledCost"))

    out: list[Diagnostic] = []
    for i, detail in enumerate(invoice_detail, start=1):
        detail_id = (detail.get("InvoiceDetailId") or "").strip()
        if not detail_id:
            continue
        invoiced = _dec(detail.get("BilledCost"))
        if detail_id not in sums:
            out.append(
                Diagnostic(
                    code="FDT-CROSS-031",
                    severity=Severity.WARNING,
                    message=f"Invoice Detail line {detail_id!r} has no matching Cost and Usage rows",
                    datasets=("Invoice Detail", "Cost and Usage"),
                    dataset="Invoice Detail",
                    line_number=i,
                    column="InvoiceDetailId",
                    value=detail_id,
                    record_keys={"InvoiceDetailId": detail_id},
                )
            )
            continue
        summed = sums[detail_id]
        if abs(summed - invoiced) > tolerance:
            out.append(
                Diagnostic(
                    code="FDT-CROSS-030",
                    severity=Severity.ERROR,
                    message=f"Invoice Detail line {detail_id!r} BilledCost does not reconcile "
                    f"with summed Cost and Usage (tolerance {tolerance})",
                    datasets=("Invoice Detail", "Cost and Usage"),
                    dataset="Invoice Detail",
                    line_number=i,
                    column="BilledCost",
                    expected=str(summed),
                    actual=str(invoiced),
                    record_keys={"InvoiceDetailId": detail_id},
                )
            )
    return out
