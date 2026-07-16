"""Deterministic generator for synthetic Azure data in the FOCUS 1.3 format.

FOCUS 1.3 (https://focus.finops.org/focus-specification/v1-3/) defines **two**
datasets; FOCUS is a *normalised* spec, so the column set is identical across
providers — only the values differ. This emits Microsoft Azure data for both:

* **Cost and Usage** — 65 columns (1.2's 57 plus the 1.3 provider split
  ``ServiceProviderName``/``HostProviderName`` with the deprecated
  ``ProviderName``/``PublisherName`` retained, the Split Cost Allocation columns,
  and ``ContractApplied``).
* **Contract Commitment** — 13 columns, joinable to Cost and Usage via
  ``ContractCommitmentId`` == ``CommitmentDiscountId``.

Synthetic / PII-free, deterministic (seeded RNG + fixed timestamps -> a
given (rows, seed) is byte-reproducible). Self-contained (standard library
only).

CLI
---
    python -m focus_data_toolkit.generators.generate_azure_focus_1_3 \
        --rows 1000 --seed 1302 \
        --out focus_sample_costandusage_azure_1000.csv
    python -m focus_data_toolkit.generators.generate_azure_focus_1_3.py --dataset contract_commitment \
        --out focus_sample_contractcommitment_azure.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from focus_data_toolkit.focus_json import dumps_object

# --------------------------------------------------------------------------- #
# Dataset 1 — Cost and Usage (65 columns)
# --------------------------------------------------------------------------- #
COLUMNS: tuple[str, ...] = (
    "ProviderName",
    "PublisherName",
    "ServiceProviderName",
    "HostProviderName",
    "InvoiceIssuerName",
    "InvoiceId",
    "BillingAccountId",
    "BillingAccountName",
    "BillingAccountType",
    "SubAccountId",
    "SubAccountName",
    "SubAccountType",
    "BillingPeriodStart",
    "BillingPeriodEnd",
    "ChargePeriodStart",
    "ChargePeriodEnd",
    "ChargeCategory",
    "ChargeClass",
    "ChargeDescription",
    "ChargeFrequency",
    "BilledCost",
    "EffectiveCost",
    "ListCost",
    "ContractedCost",
    "ListUnitPrice",
    "ContractedUnitPrice",
    "PricingCategory",
    "PricingQuantity",
    "PricingUnit",
    "PricingCurrency",
    "PricingCurrencyContractedUnitPrice",
    "PricingCurrencyEffectiveCost",
    "PricingCurrencyListUnitPrice",
    "BillingCurrency",
    "ConsumedQuantity",
    "ConsumedUnit",
    "ServiceName",
    "ServiceCategory",
    "ServiceSubcategory",
    "SkuId",
    "SkuMeter",
    "SkuPriceId",
    "SkuPriceDetails",
    "ResourceId",
    "ResourceName",
    "ResourceType",
    "RegionId",
    "RegionName",
    "AvailabilityZone",
    "CommitmentDiscountId",
    "CommitmentDiscountName",
    "CommitmentDiscountCategory",
    "CommitmentDiscountType",
    "CommitmentDiscountStatus",
    "CommitmentDiscountQuantity",
    "CommitmentDiscountUnit",
    "ContractApplied",
    "CapacityReservationId",
    "CapacityReservationStatus",
    "AllocatedMethodId",
    "AllocatedMethodDetails",
    "AllocatedResourceId",
    "AllocatedResourceName",
    "AllocatedTags",
    "Tags",
)

assert len(COLUMNS) == 65, f"FOCUS 1.3 Cost and Usage must have 65 columns, got {len(COLUMNS)}"
assert len(set(COLUMNS)) == 65, "FOCUS 1.3 Cost and Usage column names must be unique"

DEPRECATED_PROVIDER_COLUMNS: tuple[str, ...] = ("ProviderName", "PublisherName")
ALLOCATION_COLUMNS: tuple[str, ...] = (
    "AllocatedMethodId",
    "AllocatedMethodDetails",
    "AllocatedResourceId",
    "AllocatedResourceName",
    "AllocatedTags",
)
# Billing identity shared by every row of a commitment group (purchase + usage).
_BILLING_IDENTITY_KEYS: tuple[str, ...] = (
    "BillingAccountId",
    "BillingAccountName",
    "BillingAccountType",
    "SubAccountId",
    "SubAccountName",
    "SubAccountType",
    "InvoiceId",
)

# --------------------------------------------------------------------------- #
# Dataset 2 — Contract Commitment (13 columns, new in FOCUS 1.3)
# --------------------------------------------------------------------------- #
CONTRACT_COMMITMENT_COLUMNS: tuple[str, ...] = (
    "ContractCommitmentId",
    "ContractCommitmentType",
    "ContractCommitmentCategory",
    "ContractCommitmentCost",
    "ContractCommitmentQuantity",
    "ContractCommitmentUnit",
    "ContractCommitmentDescription",
    "ContractCommitmentPeriodStart",
    "ContractCommitmentPeriodEnd",
    "ContractId",
    "ContractPeriodStart",
    "ContractPeriodEnd",
    "BillingCurrency",
)

assert len(CONTRACT_COMMITMENT_COLUMNS) == 13, (
    f"FOCUS 1.3 Contract Commitment must have 13 columns, got {len(CONTRACT_COMMITMENT_COLUMNS)}"
)
assert len(set(CONTRACT_COMMITMENT_COLUMNS)) == 13, (
    "FOCUS 1.3 Contract Commitment column names must be unique"
)

PRICING_CATEGORIES: tuple[str, ...] = ("Standard", "Dynamic", "Committed", "Other")
FOCUS_SKU_PRICE_KEYS: frozenset[str] = frozenset(
    {
        "CoreCount",
        "MemorySize",
        "InstanceType",
        "InstanceSeries",
        "OperatingSystem",
        "DiskType",
        "DiskSpace",
        "DiskMaxIops",
        "GpuCount",
        "NetworkMaxIops",
        "NetworkMaxThroughput",
    }
)

DEFAULT_ROWS = 1000
DEFAULT_SEED = 1302
DEFAULT_OUT = Path("focus_sample_costandusage_azure_1000.csv")
DEFAULT_COMMITMENT_OUT = Path("focus_sample_contractcommitment_azure.csv")
PROVIDER_NAME = "Microsoft Azure"
PUBLISHER_NAME = "Microsoft"
SERVICE_PROVIDER_NAME = "Microsoft Azure"
HOST_PROVIDER_NAME = "Microsoft Azure"
INVOICE_ISSUER_NAME = "Microsoft Azure"

_BILLING_START = datetime(2026, 5, 1, tzinfo=UTC)
_BILLING_END = datetime(2026, 6, 1, tzinfo=UTC)
_PERIOD_DAYS = 28
_PERIOD_HOURS = _PERIOD_DAYS * 24
_COMMIT_TERM_DAYS = 365

_COST_Q = Decimal("0.000001")
_PRICE_Q = Decimal("0.0000000001")
_QTY_Q = Decimal("0.0001")
_EUR_PER_USD = Decimal("0.92")
_COMMIT_RATE = Decimal("0.667")
_PRIVATE_RATE = Decimal("0.90")
_COMMIT_TERM_HOURS = Decimal("8760")


@dataclass(frozen=True)
class _ServiceSpec:
    name: str
    category: str
    subcategory: str
    resource_type: str
    arm_type: str
    sku_meter: str
    pricing_unit: str
    description: str
    unit_price_usd: Decimal
    qty_low: Decimal
    qty_high: Decimal
    name_prefix: str
    granularity: str
    zonal: bool
    commitment_eligible: bool
    sku_details: dict[str, object] = field(default_factory=dict)


_SERVICES: tuple[_ServiceSpec, ...] = (
    _ServiceSpec(
        "Virtual Machines", "Compute", "Virtual Machines", "Virtual Machine",
        "Microsoft.Compute/virtualMachines", "Compute", "Hours",
        "Standard_D4s_v5 instance", Decimal("0.192"), Decimal("1"), Decimal("1"),
        "vm-", "hourly", True, True,
        {
            "InstanceType": "Standard_D4s_v5", "InstanceSeries": "Ddsv5", "CoreCount": 4,
            "MemorySize": 16, "OperatingSystem": "Linux", "x_Tenancy": "Shared",
        },
    ),
    _ServiceSpec(
        "Azure Blob Storage", "Storage", "Object Storage", "Storage Account",
        "Microsoft.Storage/storageAccounts", "Storage", "GB-Months",
        "Hot LRS data stored", Decimal("0.0184"), Decimal("50"), Decimal("8000"),
        "stor", "monthly", False, False,
        {"StorageClass": "Hot", "Redundancy": "LRS"},
    ),
    _ServiceSpec(
        "Azure SQL Database", "Databases", "Relational Databases", "SQL Database",
        "Microsoft.Sql/servers/databases", "Database", "Hours",
        "General Purpose 4 vCore", Decimal("0.504"), Decimal("1"), Decimal("1"),
        "sqldb-", "hourly", False, False,
        {"InstanceType": "GP_Gen5_4", "CoreCount": 4, "MemorySize": 20, "x_Engine": "SQLServer"},
    ),
    _ServiceSpec(
        "Azure Functions", "Compute", "Serverless Compute", "Function App",
        "Microsoft.Web/sites", "Compute", "GB-Seconds",
        "Function execution duration", Decimal("0.000016"), Decimal("100000"),
        Decimal("4000000"), "func-", "hourly", False, False,
        {"x_Plan": "Consumption", "x_Runtime": "dotnet8"},
    ),
    _ServiceSpec(
        "Azure Kubernetes Service", "Compute", "Containers", "Managed Cluster",
        "Microsoft.ContainerService/managedClusters", "Compute", "Hours",
        "AKS node pool hours", Decimal("0.192"), Decimal("1"), Decimal("1"),
        "aks-", "hourly", False, False,
        {
            "InstanceType": "Standard_D4s_v5", "CoreCount": 4, "MemorySize": 16,
            "x_NodePool": "system",
        },
    ),
    _ServiceSpec(
        "Azure Monitor", "Management and Governance", "Observability", "Log Analytics Workspace",
        "Microsoft.OperationalInsights/workspaces", "Monitoring", "GB",
        "Log data ingested", Decimal("2.30"), Decimal("1"), Decimal("500"),
        "law-", "daily", False, False, {"x_DataType": "Logs"},
    ),
    _ServiceSpec(
        "Azure Cosmos DB", "Databases", "NoSQL Databases", "Cosmos DB Account",
        "Microsoft.DocumentDB/databaseAccounts", "Database", "Hours",
        "Provisioned throughput", Decimal("0.008"), Decimal("1"), Decimal("1"),
        "cosmos-", "hourly", False, False, {"x_CapacityMode": "Provisioned"},
    ),
    _ServiceSpec(
        "Virtual Network", "Networking", "Network Connectivity", "Virtual Network",
        "Microsoft.Network/virtualNetworks", "Data Transfer", "GB",
        "VNet peering data transfer", Decimal("0.010"), Decimal("1"), Decimal("5000"),
        "vnet-", "daily", False, False, {"x_TransferType": "Peering"},
    ),
)
_VM = _SERVICES[0]
ALLOWED_SUBCATEGORIES: frozenset[str] = frozenset(s.subcategory for s in _SERVICES)

_ALLOCATION_METHODS: tuple[tuple[str, dict[str, object]], ...] = (
    ("split-proportional", {"x_Strategy": "Proportional", "x_Basis": "vCPUSeconds"}),
    ("split-even", {"x_Strategy": "Even", "x_Basis": "Workloads"}),
    ("split-weighted", {"x_Strategy": "Weighted", "x_Basis": "MemoryBytes"}),
)
_ALLOCATION_WORKLOADS = ("checkout", "search", "billing", "analytics", "ingestion")

_REGIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("eastus", "East US", ("eastus-1", "eastus-2", "eastus-3")),
    ("westeurope", "West Europe", ("westeurope-1", "westeurope-2")),
    ("westus2", "West US 2", ("westus2-1", "westus2-2")),
    ("southeastasia", "Southeast Asia", ("southeastasia-1", "southeastasia-2")),
)

_BILLING_ACCOUNTS: tuple[tuple[str, str], ...] = (
    ("8a1b2c3d-0000-4a00-9000-000000000001", "ExampleCorp MCA - Primary"),
    ("8a1b2c3d-0000-4a00-9000-000000000002", "ExampleCorp MCA - Secondary"),
)

_SUB_ACCOUNTS: tuple[tuple[str, str], ...] = (
    ("11111111-1111-4111-8111-111111111111", "prod-platform"),
    ("22222222-2222-4222-8222-222222222222", "staging-platform"),
    ("33333333-3333-4333-8333-333333333333", "data-analytics"),
    ("44444444-4444-4444-8444-444444444444", "sandbox-dev"),
)

_ENVIRONMENTS = ("prod", "staging", "dev")
_COST_CENTERS = ("cc-1042", "cc-2087", "cc-3110")
_OWNERS = ("team-platform", "team-data", "team-payments")


def _q(value: Decimal, quant: Decimal) -> Decimal:
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def _s(value: Decimal) -> str:
    return format(value, "f")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _hexid(rng: random.Random, width: int) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(width))


def _period(i: int, granularity: str) -> tuple[str, str]:
    if granularity == "hourly":
        start = _BILLING_START + timedelta(hours=i % _PERIOD_HOURS)
        return _iso(start), _iso(start + timedelta(hours=1))
    if granularity == "daily":
        start = _BILLING_START + timedelta(days=i % _PERIOD_DAYS)
        return _iso(start), _iso(start + timedelta(days=1))
    return _iso(_BILLING_START), _iso(_BILLING_END)


def _arm_id(spec: _ServiceSpec, sub_id: str, sub_name: str, name: str) -> str:
    return (
        f"/subscriptions/{sub_id}/resourceGroups/rg-{sub_name}"
        f"/providers/{spec.arm_type}/{name}"
    )


def _contract_id_for(commit_id: str) -> str:
    """Deterministic parent ContractId for a commitment id (shared by both datasets)."""
    return f"CONTRACT-{commit_id.rsplit('/', 1)[-1][:12]}"


def _contract_applied(
    commit_id: str, contract_id: str, applied_cost: str, applied_qty: str, applied_unit: str
) -> str:
    """FOCUS 1.3 ContractApplied JSON object (contractapplied.md @ v1.3): a top-level
    ``Elements`` array linking the row to the Contract Commitment dataset via
    ``ContractCommitmentID``. The applied cost/quantity are JSON numbers."""
    return dumps_object(
        {
            "Elements": [
                {
                    "ContractID": contract_id,
                    "ContractCommitmentID": commit_id,
                    "ContractCommitmentAppliedCost": applied_cost,
                    "ContractCommitmentAppliedQuantity": applied_qty,
                    "ContractCommitmentAppliedUnit": applied_unit,
                }
            ]
        },
        numeric_keys=frozenset(
            {"ContractCommitmentAppliedCost", "ContractCommitmentAppliedQuantity"}
        ),
    )


def _base_row(rng: random.Random) -> tuple[dict[str, str], dict[str, str]]:
    billing_id, billing_name = rng.choice(_BILLING_ACCOUNTS)
    sub_id, sub_name = rng.choice(_SUB_ACCOUNTS)
    row = {name: "" for name in COLUMNS}
    row["ProviderName"] = PROVIDER_NAME  # deprecated in 1.3, still emitted
    row["PublisherName"] = PUBLISHER_NAME  # deprecated in 1.3, still emitted
    row["ServiceProviderName"] = SERVICE_PROVIDER_NAME
    row["HostProviderName"] = HOST_PROVIDER_NAME
    row["InvoiceIssuerName"] = INVOICE_ISSUER_NAME
    row["InvoiceId"] = f"INV-2026-05-{billing_id[-6:]}"
    row["BillingAccountId"] = billing_id
    row["BillingAccountName"] = billing_name
    row["BillingAccountType"] = "Microsoft Customer Agreement"
    row["SubAccountId"] = sub_id
    row["SubAccountName"] = sub_name
    row["SubAccountType"] = "Subscription"
    row["BillingPeriodStart"] = _iso(_BILLING_START)
    row["BillingPeriodEnd"] = _iso(_BILLING_END)
    row["BillingCurrency"] = "USD"
    # The generator supports pricing in USD/EUR, so PricingCurrency is never null;
    # _set_currency overrides it for priced rows (Tax/Credit keep this default).
    row["PricingCurrency"] = "USD"
    row["Tags"] = json.dumps(
        {
            "Environment": rng.choice(_ENVIRONMENTS),
            "CostCenter": rng.choice(_COST_CENTERS),
            "Owner": rng.choice(_OWNERS),
        },
        separators=(",", ":"),
    )
    return row, {"billing_id": billing_id, "sub_id": sub_id, "sub_name": sub_name}


def _set_service(row: dict[str, str], spec: _ServiceSpec) -> None:
    row["ServiceName"] = spec.name
    row["ServiceCategory"] = spec.category
    row["ServiceSubcategory"] = spec.subcategory


def _set_resource_sku(
    rng: random.Random, row: dict[str, str], spec: _ServiceSpec, ctx: dict[str, str],
    region_id: str, region_name: str, resource_name: str,
) -> None:
    row["RegionId"] = region_id
    row["RegionName"] = region_name
    row["ResourceId"] = _arm_id(spec, ctx["sub_id"], ctx["sub_name"], resource_name)
    row["ResourceName"] = resource_name
    row["ResourceType"] = spec.resource_type
    row["SkuId"] = f"AZ-{spec.name[:4].upper().strip()}-{_hexid(rng, 6)}"
    row["SkuMeter"] = spec.sku_meter
    row["SkuPriceId"] = f"AZSP-{_hexid(rng, 8)}"
    row["SkuPriceDetails"] = json.dumps(spec.sku_details, separators=(",", ":"))


def _set_currency(row: dict[str, str], pricing_currency: str, list_unit: Decimal,
                  contracted_unit: Decimal, effective_cost: Decimal) -> None:
    row["PricingCurrency"] = pricing_currency
    fx = _EUR_PER_USD if pricing_currency == "EUR" else Decimal("1")
    row["PricingCurrencyListUnitPrice"] = _s(_q(list_unit * fx, _PRICE_Q))
    row["PricingCurrencyContractedUnitPrice"] = _s(_q(contracted_unit * fx, _PRICE_Q))
    row["PricingCurrencyEffectiveCost"] = _s(_q(effective_cost * fx, _COST_Q))


def _usage_row(rng: random.Random, i: int) -> dict[str, str]:
    spec = rng.choice(_SERVICES)
    region_id, region_name, azs = rng.choice(_REGIONS)
    row, ctx = _base_row(rng)
    row["ChargePeriodStart"], row["ChargePeriodEnd"] = _period(i, spec.granularity)
    _set_service(row, spec)
    resource_name = f"{spec.name_prefix}{_hexid(rng, 8)}"
    _set_resource_sku(rng, row, spec, ctx, region_id, region_name, resource_name)
    if spec.zonal:
        row["AvailabilityZone"] = rng.choice(azs)

    quantity = _q(Decimal(rng.uniform(float(spec.qty_low), float(spec.qty_high))), _QTY_Q)
    jitter = Decimal(rng.uniform(0.97, 1.03))
    list_unit = _q(spec.unit_price_usd * jitter, _PRICE_Q)
    contracted_unit = _q(list_unit * _PRIVATE_RATE, _PRICE_Q)
    list_cost = _q(list_unit * quantity, _COST_Q)
    contracted_cost = _q(contracted_unit * quantity, _COST_Q)

    row["ChargeCategory"] = "Usage"
    row["ChargeFrequency"] = "Usage-Based"
    row["ChargeDescription"] = spec.description
    row["PricingCategory"] = "Standard"
    row["BilledCost"] = _s(contracted_cost)
    row["EffectiveCost"] = _s(contracted_cost)
    row["ListCost"] = _s(list_cost)
    row["ContractedCost"] = _s(contracted_cost)
    row["ListUnitPrice"] = _s(list_unit)
    row["ContractedUnitPrice"] = _s(contracted_unit)
    row["PricingQuantity"] = _s(quantity)
    row["PricingUnit"] = spec.pricing_unit
    row["ConsumedQuantity"] = _s(quantity)
    row["ConsumedUnit"] = spec.pricing_unit
    # On-demand usage: no contract applied -> ContractApplied stays null.
    _set_currency(row, "EUR" if rng.random() < 0.10 else "USD", list_unit, contracted_unit,
                  contracted_cost)
    return row


def _split_allocation_row(rng: random.Random, i: int) -> dict[str, str]:
    """Split Cost Allocation row (FOCUS 1.3): a shared VM host's cost allocated to
    an AKS workload. ``ResourceId`` is the shared host; ``Allocated*`` name the
    workload that received the split."""
    spec = _VM
    region_id, region_name, azs = rng.choice(_REGIONS)
    row, ctx = _base_row(rng)
    row["ChargePeriodStart"], row["ChargePeriodEnd"] = _period(i, "hourly")
    _set_service(row, spec)
    shared_name = f"shared-host-{_hexid(rng, 8)}"
    _set_resource_sku(rng, row, spec, ctx, region_id, region_name, shared_name)
    row["AvailabilityZone"] = rng.choice(azs)

    quantity = _q(Decimal(rng.uniform(0.05, 1.0)), _QTY_Q)
    jitter = Decimal(rng.uniform(0.97, 1.03))
    list_unit = _q(spec.unit_price_usd * jitter, _PRICE_Q)
    contracted_unit = _q(list_unit * _PRIVATE_RATE, _PRICE_Q)
    list_cost = _q(list_unit * quantity, _COST_Q)
    contracted_cost = _q(contracted_unit * quantity, _COST_Q)

    row["ChargeCategory"] = "Usage"
    row["ChargeFrequency"] = "Usage-Based"
    row["ChargeDescription"] = "Shared VM host cost allocated to workload"
    row["PricingCategory"] = "Standard"
    row["BilledCost"] = _s(contracted_cost)
    row["EffectiveCost"] = _s(contracted_cost)
    row["ListCost"] = _s(list_cost)
    row["ContractedCost"] = _s(contracted_cost)
    row["ListUnitPrice"] = _s(list_unit)
    row["ContractedUnitPrice"] = _s(contracted_unit)
    row["PricingQuantity"] = _s(quantity)
    row["PricingUnit"] = spec.pricing_unit
    row["ConsumedQuantity"] = _s(quantity)
    row["ConsumedUnit"] = spec.pricing_unit
    # Split-allocated on-demand cost: no contract applied -> ContractApplied null.

    workload = rng.choice(_ALLOCATION_WORKLOADS)
    method_id, method_details = rng.choice(_ALLOCATION_METHODS)
    row["AllocatedMethodId"] = method_id
    # FOCUS 1.3 split allocation details: an Elements array, each entry exposing the
    # allocated ratio and the usage that drove the split (plus x_ method metadata).
    # FOCUS AllocatedRatio / UsageQuantity are Numeric -> emitted as JSON numbers.
    element = {
        "AllocatedRatio": _s(quantity),
        "UsageUnit": spec.pricing_unit,
        "UsageQuantity": _s(quantity),
        **method_details,
    }
    row["AllocatedMethodDetails"] = dumps_object(
        {"Elements": [element]},
        numeric_keys=frozenset({"AllocatedRatio", "UsageQuantity"}),
    )
    row["AllocatedResourceId"] = (
        f"/subscriptions/{ctx['sub_id']}/resourceGroups/rg-{ctx['sub_name']}"
        f"/providers/Microsoft.ContainerService/managedClusters/aks-{_hexid(rng, 6)}"
        f"/workloads/{workload}"
    )
    row["AllocatedResourceName"] = f"workload-{workload}"
    row["AllocatedTags"] = json.dumps(
        {"workload": workload, "CostCenter": rng.choice(_COST_CENTERS)},
        separators=(",", ":"),
    )
    _set_currency(row, "USD", list_unit, contracted_unit, contracted_cost)
    return row


def _standalone_purchase_row(rng: random.Random, i: int) -> dict[str, str]:
    spec = rng.choice(_SERVICES)
    region_id, region_name, _ = rng.choice(_REGIONS)
    row, ctx = _base_row(rng)
    row["ChargePeriodStart"], row["ChargePeriodEnd"] = _period(i, "daily")
    _set_service(row, spec)
    resource_name = f"{spec.name_prefix}{_hexid(rng, 8)}"
    _set_resource_sku(rng, row, spec, ctx, region_id, region_name, resource_name)
    amount = _q(Decimal(rng.uniform(20.0, 800.0)), _COST_Q)
    row["ChargeCategory"] = "Purchase"
    row["ChargeFrequency"] = "Recurring"
    row["ChargeDescription"] = f"{spec.name} subscription fee"
    row["PricingCategory"] = "Standard"
    row["BilledCost"] = _s(amount)
    row["EffectiveCost"] = "0"  # purchase covers future eligible charges
    row["ListCost"] = _s(amount)
    row["ContractedCost"] = _s(amount)
    row["ListUnitPrice"] = _s(amount)
    row["ContractedUnitPrice"] = _s(amount)
    row["PricingQuantity"] = "1"
    row["PricingUnit"] = "Units"
    _set_currency(row, "USD", amount, amount, Decimal("0"))
    return row


def _tax_row(rng: random.Random, i: int) -> dict[str, str]:
    spec = rng.choice(_SERVICES)
    row, _ = _base_row(rng)
    row["ChargePeriodStart"], row["ChargePeriodEnd"] = _period(i, "daily")
    _set_service(row, spec)
    amount = _q(Decimal(rng.uniform(0.5, 50.0)), _COST_Q)
    row["ChargeCategory"] = "Tax"
    row["ChargeFrequency"] = "One-Time"
    row["ChargeDescription"] = f"Tax for {spec.name}"
    row["BilledCost"] = _s(amount)
    row["EffectiveCost"] = _s(amount)
    row["ListCost"] = _s(amount)
    row["ContractedCost"] = _s(amount)
    # Multi-currency generator: keep PricingCurrency (USD, from _base_row) non-null.
    row["PricingCurrencyEffectiveCost"] = _s(amount)
    return row


def _credit_row(rng: random.Random, i: int) -> dict[str, str]:
    spec = rng.choice(_SERVICES)
    row, _ = _base_row(rng)
    row["ChargePeriodStart"], row["ChargePeriodEnd"] = _period(i, "daily")
    _set_service(row, spec)
    negative = _s(-_q(Decimal(rng.uniform(1.0, 100.0)), _COST_Q))
    row["ChargeCategory"] = "Credit"
    row["ChargeFrequency"] = "One-Time"
    row["ChargeDescription"] = f"Credit for {spec.name}"
    row["BilledCost"] = negative
    row["EffectiveCost"] = negative
    row["ListCost"] = negative
    row["ContractedCost"] = negative
    row["PricingCurrencyEffectiveCost"] = negative  # PricingCurrency (USD) is non-null
    return row


def _commitment_group(rng: random.Random, i0: int, remaining: int) -> list[dict[str, str]]:
    """A commitment Purchase row + linked committed-usage rows (shared CommitmentDiscountId).

    The Purchase row carries the full commitment terms, re-derived by the Contract
    Commitment dataset so the two datasets join on ``ContractCommitmentId``.
    """
    spec = _VM
    region_id, region_name, azs = rng.choice(_REGIONS)
    az = rng.choice(azs)
    spend_based = rng.random() < 0.6
    commit_kind = "savingsPlans" if spend_based else "reservations"
    commit_type = "Savings Plan" if spend_based else "Reservation"
    commit_category = "Spend" if spend_based else "Usage"
    commit_unit = "USD" if spend_based else "Hours"
    commit_name = (
        "AzureSavingsPlan-1yr-AllUpfront" if spend_based else "AzureReservation-1yr-AllUpfront"
    )

    list_unit = _q(spec.unit_price_usd, _PRICE_Q)
    commit_unit_price = _q(list_unit * _COMMIT_RATE, _PRICE_Q)
    upfront = _q(commit_unit_price * _COMMIT_TERM_HOURS, _COST_Q)
    commit_total_qty = _s(upfront) if spend_based else _s(_COMMIT_TERM_HOURS)

    purchase, ctx = _base_row(rng)
    commit_id = (
        f"/subscriptions/{ctx['sub_id']}/providers/Microsoft.BillingBenefits"
        f"/{commit_kind}/{_hexid(rng, 12)}"
    )
    purchase["ChargePeriodStart"] = _iso(_BILLING_START)
    purchase["ChargePeriodEnd"] = _iso(_BILLING_START + timedelta(hours=1))
    _set_service(purchase, spec)
    purchase["ResourceId"] = commit_id
    purchase["ResourceName"] = f"{commit_kind}-{_hexid(rng, 10)}"
    purchase["ResourceType"] = commit_type
    purchase["RegionId"] = region_id
    purchase["RegionName"] = region_name
    purchase["SkuId"] = f"AZ-COMMIT-{_hexid(rng, 6)}"
    purchase["SkuMeter"] = "Commitment"
    purchase["SkuPriceId"] = f"AZSP-{_hexid(rng, 8)}"
    purchase["SkuPriceDetails"] = json.dumps(
        {"x_Term": "P1Y", "x_PaymentOption": "AllUpfront"}, separators=(",", ":")
    )
    purchase["ChargeCategory"] = "Purchase"
    purchase["ChargeFrequency"] = "One-Time"
    purchase["ChargeDescription"] = f"{commit_type} commitment purchase (all upfront)"
    purchase["PricingCategory"] = "Standard"
    purchase["BilledCost"] = _s(upfront)
    purchase["EffectiveCost"] = "0.000000"
    purchase["ListCost"] = _s(upfront)
    purchase["ContractedCost"] = _s(upfront)
    purchase["ListUnitPrice"] = _s(upfront)
    purchase["ContractedUnitPrice"] = _s(upfront)
    purchase["PricingQuantity"] = "1"
    purchase["PricingUnit"] = "Units"
    purchase["CommitmentDiscountId"] = commit_id
    purchase["CommitmentDiscountName"] = commit_name
    purchase["CommitmentDiscountCategory"] = commit_category
    purchase["CommitmentDiscountType"] = commit_type
    purchase["CommitmentDiscountQuantity"] = commit_total_qty
    purchase["CommitmentDiscountUnit"] = commit_unit
    _set_currency(purchase, "USD", upfront, upfront, Decimal("0"))

    # Full billing identity reused verbatim by every linked usage row so account /
    # invoice grouping and reconciliation stay consistent within the group.
    billing_identity = {key: purchase[key] for key in _BILLING_IDENTITY_KEYS}
    contract_id = _contract_id_for(commit_id)

    rows = [purchase]
    n_usage = min(remaining - 1, rng.randint(5, 9))
    for k in range(n_usage):
        usage, _ = _base_row(rng)
        usage.update(billing_identity)
        usage["ChargePeriodStart"], usage["ChargePeriodEnd"] = _period(i0 + 1 + k, "hourly")
        _set_service(usage, spec)
        resource_name = f"{spec.name_prefix}{k:04d}{_hexid(rng, 6)}"
        usage["ResourceId"] = _arm_id(spec, ctx["sub_id"], ctx["sub_name"], resource_name)
        usage["ResourceName"] = resource_name
        usage["ResourceType"] = spec.resource_type
        usage["RegionId"] = region_id
        usage["RegionName"] = region_name
        usage["AvailabilityZone"] = az
        usage["SkuId"] = f"AZ-{spec.name[:4].upper().strip()}-{_hexid(rng, 6)}"
        usage["SkuMeter"] = spec.sku_meter
        usage["SkuPriceId"] = f"AZSP-{_hexid(rng, 8)}"
        usage["SkuPriceDetails"] = json.dumps(spec.sku_details, separators=(",", ":"))
        list_cost = _q(list_unit, _COST_Q)
        effective = _q(commit_unit_price, _COST_Q)
        usage["ChargeCategory"] = "Usage"
        usage["ChargeFrequency"] = "Usage-Based"
        usage["ChargeDescription"] = f"{spec.name} committed usage"
        usage["PricingCategory"] = "Committed"
        usage["BilledCost"] = "0.000000"
        usage["EffectiveCost"] = _s(effective)
        usage["ListCost"] = _s(list_cost)
        usage["ContractedCost"] = _s(effective)
        usage["ListUnitPrice"] = _s(list_unit)
        usage["ContractedUnitPrice"] = _s(commit_unit_price)
        usage["PricingQuantity"] = "1.0000"
        usage["PricingUnit"] = "Hours"
        usage["ConsumedQuantity"] = "1.0000"
        usage["ConsumedUnit"] = "Hours"
        usage["CommitmentDiscountId"] = commit_id
        usage["CommitmentDiscountName"] = commit_name
        usage["CommitmentDiscountCategory"] = commit_category
        usage["CommitmentDiscountType"] = commit_type
        usage["CommitmentDiscountStatus"] = "Used"
        usage["CommitmentDiscountQuantity"] = _s(effective) if spend_based else "1.0000"
        usage["CommitmentDiscountUnit"] = commit_unit
        # FOCUS 1.3 ContractApplied: the JSON link to the Contract Commitment dataset.
        usage["ContractApplied"] = _contract_applied(
            commit_id, contract_id, _s(effective), "1.0000", "Hours"
        )
        _set_currency(usage, "USD", list_unit, commit_unit_price, effective)
        rows.append(usage)
    return rows


def generate_rows(
    rows: int = DEFAULT_ROWS,
    seed: int = DEFAULT_SEED,
    *,
    include_credits: bool = False,
) -> list[dict[str, str]]:
    """Return ``rows`` synthetic Azure FOCUS 1.3 Cost and Usage records."""
    if rows < 1:
        raise ValueError("rows must be >= 1")
    rng = random.Random(seed)
    out: list[dict[str, str]] = []
    while len(out) < rows:
        i = len(out)
        remaining = rows - i
        roll = rng.random()
        if include_credits and roll < 0.05:
            out.append(_credit_row(rng, i))
        elif roll < 0.12:
            out.append(_tax_row(rng, i))
        elif roll < 0.20:
            out.append(_standalone_purchase_row(rng, i))
        elif roll < 0.40:
            out.append(_split_allocation_row(rng, i))
        elif roll < 0.58 and remaining >= 6:
            out.extend(_commitment_group(rng, i, remaining))
        else:
            out.append(_usage_row(rng, i))
    return out[:rows]


def generate_csv_bytes(
    rows: int = DEFAULT_ROWS,
    seed: int = DEFAULT_SEED,
    *,
    include_credits: bool = False,
) -> bytes:
    """Serialise the Cost and Usage rows to deterministic UTF-8 CSV bytes (LF)."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(COLUMNS), lineterminator="\n")
    writer.writeheader()
    for record in generate_rows(rows, seed, include_credits=include_credits):
        writer.writerow(record)
    return buffer.getvalue().encode("utf-8")


# --------------------------------------------------------------------------- #
# Dataset 2 — Contract Commitment (derived from the Cost and Usage commitments)
# --------------------------------------------------------------------------- #
def generate_contract_commitment_rows(
    rows: int = DEFAULT_ROWS,
    seed: int = DEFAULT_SEED,
) -> list[dict[str, str]]:
    """Return the Contract Commitment dataset for the same (rows, seed); one row per
    commitment, keyed so ``ContractCommitmentId`` == ``CommitmentDiscountId``."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for cu in generate_rows(rows, seed):
        if cu["ChargeCategory"] != "Purchase" or not cu["CommitmentDiscountId"]:
            continue
        commit_id = cu["CommitmentDiscountId"]
        if commit_id in seen:
            continue
        seen.add(commit_id)
        period_start = _parse_iso(cu["ChargePeriodStart"])
        period_end = period_start + timedelta(days=_COMMIT_TERM_DAYS)
        contract_id = _contract_id_for(commit_id)
        row = {name: "" for name in CONTRACT_COMMITMENT_COLUMNS}
        row["ContractCommitmentId"] = commit_id
        row["ContractCommitmentType"] = cu["CommitmentDiscountType"]
        row["ContractCommitmentCategory"] = cu["CommitmentDiscountCategory"]
        row["ContractCommitmentCost"] = cu["BilledCost"]
        row["ContractCommitmentQuantity"] = cu["CommitmentDiscountQuantity"]
        row["ContractCommitmentUnit"] = cu["CommitmentDiscountUnit"]
        row["ContractCommitmentDescription"] = cu["CommitmentDiscountName"]
        row["ContractCommitmentPeriodStart"] = _iso(period_start)
        row["ContractCommitmentPeriodEnd"] = _iso(period_end)
        row["ContractId"] = contract_id
        row["ContractPeriodStart"] = _iso(period_start)
        row["ContractPeriodEnd"] = _iso(period_end)
        row["BillingCurrency"] = "USD"
        out.append(row)
    return out


def generate_contract_commitment_csv_bytes(
    rows: int = DEFAULT_ROWS,
    seed: int = DEFAULT_SEED,
) -> bytes:
    """Serialise the Contract Commitment dataset to deterministic UTF-8 CSV bytes (LF)."""
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(CONTRACT_COMMITMENT_COLUMNS), lineterminator="\n"
    )
    writer.writeheader()
    for record in generate_contract_commitment_rows(rows, seed):
        writer.writerow(record)
    return buffer.getvalue().encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic Azure FOCUS 1.3 CSV data.")
    parser.add_argument(
        "--dataset",
        choices=("cost_and_usage", "contract_commitment"),
        default="cost_and_usage",
        help="FOCUS 1.3 dataset to emit (default: cost_and_usage)",
    )
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS, help="number of Cost/Usage rows")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="deterministic RNG seed")
    parser.add_argument("--out", type=Path, default=None, help="output CSV path")
    parser.add_argument(
        "--include-credits",
        action="store_true",
        help="emit some Credit rows with negative BilledCost (excluded from the default fixture)",
    )
    args = parser.parse_args(argv)

    if args.dataset == "contract_commitment":
        payload = generate_contract_commitment_csv_bytes(args.rows, args.seed)
        out = args.out or DEFAULT_COMMITMENT_OUT
        columns = CONTRACT_COMMITMENT_COLUMNS
    else:
        payload = generate_csv_bytes(args.rows, args.seed, include_credits=args.include_credits)
        out = args.out or DEFAULT_OUT
        columns = COLUMNS

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    print(f"Wrote {args.dataset} ({len(columns)} Azure FOCUS 1.3 columns) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
