"""FOCUS-defined property keys for JSON / Key-Value typed columns.

Sourced verbatim from the frozen FOCUS specification tags
(``FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec`` at ``v1.3`` / ``v1.4``). These
registries drive the ``x_`` custom-key rule: inside a FOCUS JSON column, any key
that is **not** a FOCUS-defined property MUST be prefixed with ``x_`` (FOCUS
attributes ``CustomColumnHandling`` / per-column ``KeyValueFormat`` /
``JsonObjectFormat``).

Important exceptions: ``Tags`` and ``AllocatedTags`` are Key-Value columns whose
keys are arbitrary user tag names — the ``x_`` rule does **not** apply to them
(FOCUS ``tags.md``: a single user-defined tag scheme carries no prefix). They are
deliberately absent from :data:`XPREFIX_ENFORCED_COLUMNS`.
"""

from __future__ import annotations

# --- Key-Value columns with a FOCUS-defined property set ------------------- #

# SkuPriceDetails FOCUS-defined properties (identical in v1.3 and v1.4):
# skupricedetails.md "FOCUS-Defined Properties" table — 13 keys.
SKU_PRICE_DETAILS_KEYS: frozenset[str] = frozenset(
    {
        "CoreCount",
        "DiskMaxIops",
        "DiskSpace",
        "DiskType",
        "GpuCount",
        "InstanceType",
        "InstanceSeries",
        "MemorySize",
        "NetworkMaxIops",
        "NetworkMaxThroughput",
        "OperatingSystem",
        "Redundancy",
        "StorageClass",
    }
)

# InvoiceDetailGrain FOCUS-defined properties (v1.4 invoicedetailgrain.md).
# Each links to a Cost and Usage column; the JSON key is that column's Column ID.
INVOICE_DETAIL_GRAIN_KEYS: frozenset[str] = frozenset(
    {
        "ContractId",
        "RegionId",
        "ResourceId",
        "ResourceType",
        "ServiceName",
        "SkuId",
        "SkuMeter",
        "SkuPriceId",
        "SubAccountId",
    }
)

# --- JSON-Object columns with a top-level ``Elements`` array --------------- #

# ContractApplied element keys. The identifier keys changed casing between
# versions (contractapplied.md @ v1.3 vs v1.4); the three metric keys are stable.
CONTRACT_APPLIED_ELEMENT_KEYS_1_3: frozenset[str] = frozenset(
    {
        "ContractID",
        "ContractCommitmentID",
        "ContractCommitmentAppliedCost",
        "ContractCommitmentAppliedQuantity",
        "ContractCommitmentAppliedUnit",
    }
)
CONTRACT_APPLIED_ELEMENT_KEYS_1_4: frozenset[str] = frozenset(
    {
        "ContractId",
        "ContractCommitmentId",
        "ContractCommitmentAppliedCost",
        "ContractCommitmentAppliedQuantity",
        "ContractCommitmentAppliedUnit",
    }
)

# AllocatedMethodDetails element keys (allocatedmethoddetails.md, v1.3 & v1.4).
ALLOCATED_METHOD_DETAILS_ELEMENT_KEYS: frozenset[str] = frozenset(
    {
        "AllocatedRatio",
        "UsageUnit",
        "UsageQuantity",
    }
)

# Columns whose custom keys MUST be ``x_``-prefixed, mapped to their FOCUS-defined
# key set. Key-Value columns match keys at the top level; ``Elements`` columns
# match keys inside each element object. Tags / AllocatedTags are intentionally
# excluded (arbitrary tag keys are allowed).
XPREFIX_ENFORCED_KEYVALUE_COLUMNS: dict[str, frozenset[str]] = {
    "SkuPriceDetails": SKU_PRICE_DETAILS_KEYS,
    "InvoiceDetailGrain": INVOICE_DETAIL_GRAIN_KEYS,
}
XPREFIX_ENFORCED_ELEMENTS_COLUMNS: dict[str, frozenset[str]] = {
    "ContractApplied": CONTRACT_APPLIED_ELEMENT_KEYS_1_4,
    "AllocatedMethodDetails": ALLOCATED_METHOD_DETAILS_ELEMENT_KEYS,
}
