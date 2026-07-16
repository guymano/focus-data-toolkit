"""Derive the FOCUS 1.4 Billing Period dataset (6 columns) from Cost and Usage.

FOCUS 1.2/1.3 sources have no Billing Period dataset: it is new in 1.4. Each distinct
``(BillingPeriodStart, BillingPeriodEnd, InvoiceIssuerName)`` seen in the source becomes one
Billing Period row. The issuer is taken from each row itself (never from a global first-row
fallback); a row with no issuer keeps it empty, which the lint then flags rather than the
converter silently inventing one. Timestamps come from the period and the status of a period
present in historical billing data is ``"Closed"`` — the derivation is pure (no clock).
"""

from __future__ import annotations

from collections.abc import Sequence

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


def billing_period_row(
    start: str, end: str, issuer: str, target: Sequence[str]
) -> dict[str, str]:
    """Build one Billing Period row from a ``(start, end, issuer)`` key (pure function)."""
    values = {
        "BillingPeriodStart": start,
        "BillingPeriodEnd": end,
        "BillingPeriodCreated": start,
        "BillingPeriodLastUpdated": end,
        "BillingPeriodStatus": "Closed",
        "InvoiceIssuerName": issuer,
    }
    return {col: values.get(col, "") for col in target}


def build_billing_periods(cau_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return one Billing Period row per distinct ``(start, end, issuer)`` in ``cau_rows``."""
    target = dataset_columns(DATASET)
    seen: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in cau_rows:
        start = (row.get("BillingPeriodStart") or "").strip()
        end = (row.get("BillingPeriodEnd") or "").strip()
        issuer = (row.get("InvoiceIssuerName") or "").strip()
        if not start or not end:
            continue
        key = (start, end, issuer)
        if key in seen:
            continue
        seen[key] = billing_period_row(start, end, issuer, target)
    return [seen[key] for key in sorted(seen)]
