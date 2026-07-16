"""Derive the FOCUS 1.4 Invoice Detail dataset from Cost and Usage rows.

FOCUS 1.2/1.3 sources have no Invoice Detail dataset: it is new in 1.4. Each
invoice line item is derived by grouping the source Cost and Usage rows by
``(InvoiceId, ChargeCategory)`` (rows without an ``InvoiceId`` cannot belong
to an invoice and are skipped):

* ``BilledCost`` is the exact Decimal sum of the group's billed costs, so the
  Invoice Detail dataset reconciles with Cost and Usage by construction.
* ``InvoiceDetailId`` is a deterministic hash of the group key; the converted
  Cost and Usage rows carry the same id, linking both datasets.
* Timestamps come from the billing period (``Created`` / ``LastUpdated`` /
  ``InvoiceIssueDate`` = period end); ``InvoiceIssueStatus="Issued"`` and
  ``PaymentTerms="Net 30"`` are deterministic documented defaults.
* ``ReferenceInvoiceId`` — the committed model marks it non-nullable; normal
  (non-correction) invoices reference themselves.
* Conditional non-nullable columns with no source equivalent
  (``PaymentCurrency``, ``PaymentCurrencyBilledCost``,
  ``PaymentCurrencyInvoiceDetailId``, ``PurchaseOrderNumber``) are omitted,
  as the model allows for conditional columns.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from focus_data_toolkit.model import dataset_columns
from focus_data_toolkit.provenance import ColumnRule, Lineage

DATASET = "Invoice Detail"

# Provenance of every emitted Invoice Detail column. Invoice-issuer facts (status,
# terms, issuer-assigned id, provider timestamps, correction linkage) are not derivable
# from Cost and Usage -> ASSUMED (block strict). BilledCost is a real aggregation.
PROVENANCE: dict[str, ColumnRule] = {
    "InvoiceDetailId": ColumnRule(
        Lineage.ASSUMED, note="locally generated hash; spec: issuer-assigned id"
    ),
    "InvoiceId": ColumnRule(Lineage.OBSERVED, "CostAndUsage.InvoiceId"),
    "ReferenceInvoiceId": ColumnRule(
        Lineage.ASSUMED, note="self-reference; real correction linkage unknown"
    ),
    "ChargeCategory": ColumnRule(Lineage.OBSERVED, "CostAndUsage.ChargeCategory"),
    "BilledCost": ColumnRule(Lineage.DERIVED, "sum(CostAndUsage.BilledCost)"),
    "BillingAccountId": ColumnRule(Lineage.OBSERVED, "CostAndUsage.BillingAccountId"),
    "BillingCurrency": ColumnRule(Lineage.OBSERVED, "CostAndUsage.BillingCurrency"),
    "BillingPeriodStart": ColumnRule(Lineage.OBSERVED, "CostAndUsage.BillingPeriodStart"),
    "BillingPeriodEnd": ColumnRule(Lineage.OBSERVED, "CostAndUsage.BillingPeriodEnd"),
    "InvoiceDetailCreated": ColumnRule(Lineage.ASSUMED, note="provider record timestamp"),
    "InvoiceDetailLastUpdated": ColumnRule(Lineage.ASSUMED, note="provider record timestamp"),
    "InvoiceDetailDescription": ColumnRule(Lineage.ASSUMED, note="synthesized description"),
    "InvoiceDetailGrain": ColumnRule(Lineage.ASSUMED, note="synthetic aggregation grain (x_ keys)"),
    "InvoiceIssueDate": ColumnRule(Lineage.ASSUMED, note="provider invoice issue date"),
    "InvoiceIssueStatus": ColumnRule(
        Lineage.ASSUMED, note="provider publication state; assumed 'Issued'"
    ),
    "InvoiceIssuerName": ColumnRule(Lineage.OBSERVED, "CostAndUsage.InvoiceIssuerName"),
    "PaymentDueDate": ColumnRule(Lineage.UNAVAILABLE, note="emitted null"),
    "PaymentTerms": ColumnRule(Lineage.ASSUMED, note="assumed 'Net 30'"),
}

_OMITTED_CONDITIONAL = frozenset(
    {
        "PaymentCurrency",
        "PaymentCurrencyBilledCost",
        "PaymentCurrencyInvoiceDetailId",
        "PurchaseOrderNumber",
    }
)

# InvoiceDetailGrain is Key-Value Format; non-FOCUS-defined keys MUST be x_-prefixed
# (invoicedetailgrain.md @ v1.4). This grain is a synthetic aggregation descriptor, not
# a real invoice-line grain, so both keys are custom (x_).
_GRAIN = json.dumps(
    {"x_GroupedBy": "InvoiceId,ChargeCategory", "x_DerivedFrom": "FOCUS 1.x Cost and Usage aggregation"},
    separators=(",", ":"),
)

_COST_QUANTUM = Decimal("0.000001")


def invoice_detail_id(invoice_id: str, charge_category: str) -> str:
    """Deterministic InvoiceDetailId for one ``(InvoiceId, ChargeCategory)`` group."""
    digest = hashlib.sha256(f"{invoice_id}|{charge_category}".encode()).hexdigest()
    return f"idl-{digest[:16]}"


def build_invoice_details(
    cau_rows: list[dict[str, str]],
    *,
    invoice_issuer_name: str,
) -> tuple[list[dict[str, str]], dict[tuple[str, str], str]]:
    """Return ``(invoice_detail_rows, id_mapping)`` derived from ``cau_rows``.

    ``id_mapping`` maps ``(InvoiceId, ChargeCategory)`` to the assigned
    ``InvoiceDetailId`` so the Cost and Usage converter can back-link rows.
    """
    emitted = [c for c in dataset_columns(DATASET) if c not in _OMITTED_CONDITIONAL]
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in cau_rows:
        invoice_id = (row.get("InvoiceId") or "").strip()
        if not invoice_id:
            continue
        charge_category = (row.get("ChargeCategory") or "").strip()
        groups.setdefault((invoice_id, charge_category), []).append(row)

    rows_out: list[dict[str, str]] = []
    mapping: dict[tuple[str, str], str] = {}
    for (invoice_id, charge_category), members in sorted(groups.items()):
        detail_id = invoice_detail_id(invoice_id, charge_category)
        mapping[(invoice_id, charge_category)] = detail_id
        first = members[0]
        billed = sum(Decimal(m.get("BilledCost") or "0") for m in members)
        period_end = first.get("BillingPeriodEnd", "")
        values = {
            "InvoiceDetailId": detail_id,
            "InvoiceId": invoice_id,
            "ReferenceInvoiceId": invoice_id,
            "ChargeCategory": charge_category,
            "BilledCost": str(billed.quantize(_COST_QUANTUM)),
            "BillingAccountId": first.get("BillingAccountId", ""),
            "BillingCurrency": first.get("BillingCurrency", ""),
            "BillingPeriodStart": first.get("BillingPeriodStart", ""),
            "BillingPeriodEnd": period_end,
            "InvoiceDetailCreated": period_end,
            "InvoiceDetailLastUpdated": period_end,
            "InvoiceDetailDescription": f"{charge_category} charges for invoice {invoice_id}",
            "InvoiceDetailGrain": _GRAIN,
            "InvoiceIssueDate": period_end,
            "InvoiceIssueStatus": "Issued",
            "InvoiceIssuerName": first.get("InvoiceIssuerName") or invoice_issuer_name,
            "PaymentDueDate": "",
            "PaymentTerms": "Net 30",
        }
        rows_out.append({col: values.get(col, "") for col in emitted})
    return rows_out, mapping
