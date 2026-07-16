"""Derive the FOCUS 1.4 Billing Period dataset (6 columns) from Cost and Usage.

FOCUS 1.2/1.3 sources have no Billing Period dataset: it is new in 1.4. Each
distinct ``(BillingPeriodStart, BillingPeriodEnd, InvoiceIssuerName)`` seen in
the source Cost and Usage rows becomes one Billing Period row. Timestamps are
taken from the period itself (``Created`` = period start, ``LastUpdated`` =
period end) and the status of a period present in historical billing data is
``"Closed"`` — the derivation is pure (no clock) so conversion stays
deterministic.
"""

from __future__ import annotations

from focus_data_toolkit.model import dataset_columns
from focus_data_toolkit.provenance import ColumnRule, Lineage

DATASET = "Billing Period"

# Provenance of every Billing Period column. The status/timestamps are provider
# billing-cycle facts, not derivable from Cost and Usage -> ASSUMED (block strict).
PROVENANCE: dict[str, ColumnRule] = {
    "BillingPeriodStart": ColumnRule(Lineage.OBSERVED, "CostAndUsage.BillingPeriodStart"),
    "BillingPeriodEnd": ColumnRule(Lineage.OBSERVED, "CostAndUsage.BillingPeriodEnd"),
    "InvoiceIssuerName": ColumnRule(Lineage.OBSERVED, "CostAndUsage.InvoiceIssuerName"),
    "BillingPeriodCreated": ColumnRule(Lineage.ASSUMED, note="provider billing-cycle timestamp"),
    "BillingPeriodLastUpdated": ColumnRule(
        Lineage.ASSUMED, note="provider billing-cycle timestamp"
    ),
    "BillingPeriodStatus": ColumnRule(
        Lineage.ASSUMED, note="provider Open/Closed state; assumed 'Closed'"
    ),
}


def build_billing_periods(
    cau_rows: list[dict[str, str]],
    *,
    invoice_issuer_name: str,
) -> list[dict[str, str]]:
    """Return one Billing Period row per distinct billing period in ``cau_rows``."""
    target = dataset_columns(DATASET)
    seen: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in cau_rows:
        start = row.get("BillingPeriodStart", "")
        end = row.get("BillingPeriodEnd", "")
        issuer = row.get("InvoiceIssuerName") or invoice_issuer_name
        if not start or not end:
            continue
        key = (start, end, issuer)
        if key in seen:
            continue
        values = {
            "BillingPeriodStart": start,
            "BillingPeriodEnd": end,
            "BillingPeriodCreated": start,
            "BillingPeriodLastUpdated": end,
            "BillingPeriodStatus": "Closed",
            "InvoiceIssuerName": issuer,
        }
        seen[key] = {col: values.get(col, "") for col in target}
    return [seen[key] for key in sorted(seen)]
