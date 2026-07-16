"""Derive the FOCUS 1.4 Invoice Detail dataset from Cost and Usage rows.

FOCUS 1.2/1.3 sources have no Invoice Detail dataset: it is new in 1.4. Each invoice line
item is derived by grouping the source Cost and Usage rows on the full **business grain** —
``(InvoiceIssuerName, InvoiceId, BillingAccountId, BillingCurrency, BillingPeriodStart,
BillingPeriodEnd, ChargeCategory)`` — not on ``(InvoiceId, ChargeCategory)`` alone. The
richer key prevents merging genuinely-distinct lines when a source consolidates multiple
issuers, accounts, currencies or periods (rows without an ``InvoiceId`` are skipped).

* ``BilledCost`` is the exact Decimal sum of the group, so Invoice Detail reconciles with
  Cost and Usage by construction.
* ``InvoiceDetailId`` is a **locally generated** id (``x_fdt_idl_v1_<hash>``): the prefix and
  namespace mark it as toolkit-generated and the ``v1`` embeds the id-algorithm version, so it
  is never mistaken for a real issuer-assigned id. The converted Cost and Usage rows carry the
  same id, linking both datasets.
* Timestamps come from the billing period; ``InvoiceIssueStatus="Issued"`` /
  ``PaymentTerms="Net 30"`` are deterministic documented defaults; ``ReferenceInvoiceId``
  self-references (real correction linkage is unknown here).
* Conditional non-nullable columns with no source equivalent are omitted (the model allows it).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal

from focus_data_toolkit.model import dataset_columns
from focus_data_toolkit.provenance import ColumnRule, Lineage

DATASET = "Invoice Detail"

# The business grain a synthetic invoice line is aggregated on, in key order.
GRAIN_FIELDS: tuple[str, ...] = (
    "InvoiceIssuerName",
    "InvoiceId",
    "BillingAccountId",
    "BillingCurrency",
    "BillingPeriodStart",
    "BillingPeriodEnd",
    "ChargeCategory",
)
# Position of InvoiceId within a grain key (used to skip rows without one).
_INVOICE_ID_POS = GRAIN_FIELDS.index("InvoiceId")

# Locally-generated id namespace + algorithm version. Bump the version if the id derivation
# changes so ids from different algorithm versions never collide silently.
_LOCAL_ID_NAMESPACE = "x_fdt_idl"
_ID_ALGO_VERSION = "v1"

PROVENANCE: dict[str, ColumnRule] = {
    "InvoiceDetailId": ColumnRule(
        Lineage.ASSUMED, note="locally generated id (x_fdt_idl_v1_*); spec: issuer-assigned id"
    ),
    "InvoiceId": ColumnRule(Lineage.OBSERVED, "CostAndUsage.InvoiceId"),
    "ReferenceInvoiceId": ColumnRule(
        Lineage.ASSUMED, note="self-reference; real correction linkage unknown"
    ),
    "ChargeCategory": ColumnRule(Lineage.OBSERVED, "CostAndUsage.ChargeCategory"),
    "BilledCost": ColumnRule(Lineage.DERIVED, "sum(CostAndUsage.BilledCost) over the business grain"),
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
# (invoicedetailgrain.md @ v1.4). This grain is a synthetic aggregation descriptor.
_GRAIN = json.dumps(
    {
        "x_GroupedBy": ",".join(GRAIN_FIELDS),
        "x_DerivedFrom": "FOCUS 1.x Cost and Usage aggregation",
        "x_Generator": f"focus-data-toolkit {_LOCAL_ID_NAMESPACE}_{_ID_ALGO_VERSION}",
    },
    separators=(",", ":"),
)

_COST_QUANTUM = Decimal("0.000001")

GrainKey = tuple[str, ...]


def invoice_detail_grain_key(row: Mapping[str, str]) -> GrainKey:
    """Return the (stripped) business-grain key of a Cost and Usage row."""
    return tuple((row.get(field) or "").strip() for field in GRAIN_FIELDS)


def invoice_detail_id(grain_key: GrainKey) -> str:
    """Deterministic, clearly-local InvoiceDetailId for one business-grain group.

    Format ``x_fdt_idl_v1_<16 hex>``: the ``x_fdt_idl`` namespace and ``v1`` algorithm version
    make it unmistakably toolkit-generated, and the hash covers the full grain so two distinct
    (issuer, invoice, account, currency, period, category) lines never share an id.
    """
    # JSON-encode the key so a field containing the delimiter cannot forge a collision
    # (e.g. ("A|B","C") vs ("A","B|C") hash differently).
    payload = json.dumps(
        [_LOCAL_ID_NAMESPACE, _ID_ALGO_VERSION, list(grain_key)], separators=(",", ":")
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{_LOCAL_ID_NAMESPACE}_{_ID_ALGO_VERSION}_{digest}"


def invoice_detail_row_from_group(
    grain_key: GrainKey,
    members: Sequence[Mapping[str, str]],
    detail_id: str,
    emitted: Sequence[str],
) -> dict[str, str]:
    """Build one Invoice Detail row from a business-grain group (pure function).

    Every identity field comes from the grain key itself (not an arbitrary member row), so the
    row is fully determined by ``(grain_key, member costs)``. Shared by the eager and the
    streaming pipelines, which is what makes them provably equivalent.
    """
    issuer, invoice_id, account, currency, period_start, period_end, charge_category = grain_key
    billed = sum((Decimal(m.get("BilledCost") or "0") for m in members), Decimal(0))
    values = {
        "InvoiceDetailId": detail_id,
        "InvoiceId": invoice_id,
        "ReferenceInvoiceId": invoice_id,
        "ChargeCategory": charge_category,
        "BilledCost": str(billed.quantize(_COST_QUANTUM)),
        "BillingAccountId": account,
        "BillingCurrency": currency,
        "BillingPeriodStart": period_start,
        "BillingPeriodEnd": period_end,
        "InvoiceDetailCreated": period_end,
        "InvoiceDetailLastUpdated": period_end,
        "InvoiceDetailDescription": f"{charge_category} charges for invoice {invoice_id}",
        "InvoiceDetailGrain": _GRAIN,
        "InvoiceIssueDate": period_end,
        "InvoiceIssueStatus": "Issued",
        "InvoiceIssuerName": issuer,
        "PaymentDueDate": "",
        "PaymentTerms": "Net 30",
    }
    return {col: values.get(col, "") for col in emitted}


def build_invoice_details(
    cau_rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], dict[GrainKey, str]]:
    """Return ``(invoice_detail_rows, id_mapping)`` derived from ``cau_rows``.

    ``id_mapping`` maps each business-grain key to its assigned ``InvoiceDetailId`` so the
    Cost and Usage converter can back-link rows on exactly the same key.
    """
    emitted = [c for c in dataset_columns(DATASET) if c not in _OMITTED_CONDITIONAL]
    groups: dict[GrainKey, list[dict[str, str]]] = {}
    for row in cau_rows:
        key = invoice_detail_grain_key(row)
        if not key[_INVOICE_ID_POS]:
            continue  # a row with no InvoiceId cannot belong to an invoice line
        groups.setdefault(key, []).append(row)

    rows_out: list[dict[str, str]] = []
    mapping: dict[GrainKey, str] = {}
    for key, members in sorted(groups.items()):
        detail_id = invoice_detail_id(key)
        mapping[key] = detail_id
        rows_out.append(invoice_detail_row_from_group(key, members, detail_id, emitted))
    return rows_out, mapping
