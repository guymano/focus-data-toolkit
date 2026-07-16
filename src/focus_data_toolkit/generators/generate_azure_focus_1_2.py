"""Deterministic generator for synthetic Azure data in the FOCUS 1.2 format.

FOCUS 1.2 (https://focus.finops.org/focus-specification/v1-2/) defines exactly
57 columns; FOCUS is a *normalised* spec, so the column set is identical across
providers — only the values differ. This module emits conformant, realistic
Microsoft Azure cost-and-usage rows, grounded in the FOCUS spec sample data.

Realism + conformance (verified against the FOCUS v1.2 spec source)
------------------------------------------------------------------
* FOCUS Column IDs (`ProviderName`/`PublisherName`/`InvoiceIssuerName`,
  `RegionId`/`RegionName`).
* `UnitFormat`-compliant units ("Hours", "GB-Months", "GB-Seconds", "GB", ...).
* `SkuPriceDetails` uses FOCUS-defined keys where applicable (InstanceType,
  InstanceSeries, CoreCount, MemorySize, OperatingSystem) plus `x_` keys.
* Realistic commitment model: a Purchase row for the commitment plus committed
  Usage rows sharing its `CommitmentDiscountId`, BilledCost=0, EffectiveCost <
  ListCost (amortised rate).
* Per-column conditionality: PricingCategory in {Standard, Committed}, null for
  Tax; SkuId/SkuPriceId null for Tax; ConsumedQuantity null unless Usage with
  status != Unused; AvailabilityZone only for zonal (VM) rows; ChargeClass null;
  spend-based commitments in currency.
* Compute/serverless hourly, storage monthly, the rest daily.

Synthetic / PII-free, deterministic (seeded RNG + fixed timestamps -> a
given (rows, seed) is byte-reproducible). Self-contained (standard library
only).

CLI
---
    python -m focus_data_toolkit.generators.generate_azure_focus_1_2 \
        --rows 1000 --seed 1202 \
        --out focus_sample_costandusage_azure_1000.csv
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

COLUMNS: tuple[str, ...] = (
    "ProviderName",
    "PublisherName",
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
    "CapacityReservationId",
    "CapacityReservationStatus",
    "Tags",
)

assert len(COLUMNS) == 57, f"FOCUS 1.2 must have 57 columns, got {len(COLUMNS)}"
assert len(set(COLUMNS)) == 57, "FOCUS 1.2 column names must be unique"

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
DEFAULT_SEED = 1202
DEFAULT_OUT = Path("focus_sample_costandusage_azure_1000.csv")
PROVIDER_NAME = "Microsoft Azure"
PUBLISHER_NAME = "Microsoft"
INVOICE_ISSUER_NAME = "Microsoft Azure"

_BILLING_START = datetime(2026, 5, 1, tzinfo=UTC)
_BILLING_END = datetime(2026, 6, 1, tzinfo=UTC)
_PERIOD_DAYS = 28
_PERIOD_HOURS = _PERIOD_DAYS * 24

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


def _base_row(rng: random.Random) -> tuple[dict[str, str], dict[str, str]]:
    billing_id, billing_name = rng.choice(_BILLING_ACCOUNTS)
    sub_id, sub_name = rng.choice(_SUB_ACCOUNTS)
    row = {name: "" for name in COLUMNS}
    row["ProviderName"] = PROVIDER_NAME
    row["PublisherName"] = PUBLISHER_NAME
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
    _set_currency(row, "EUR" if rng.random() < 0.10 else "USD", list_unit, contracted_unit,
                  contracted_cost)
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
    return row


def _commitment_group(rng: random.Random, i0: int, remaining: int) -> list[dict[str, str]]:
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

    rows = [purchase]
    n_usage = min(remaining - 1, rng.randint(5, 9))
    for k in range(n_usage):
        usage, _ = _base_row(rng)
        usage["BillingAccountId"] = ctx["billing_id"]
        usage["SubAccountId"] = ctx["sub_id"]
        usage["SubAccountName"] = ctx["sub_name"]
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
        _set_currency(usage, "USD", list_unit, commit_unit_price, effective)
        rows.append(usage)
    return rows


def generate_rows(
    rows: int = DEFAULT_ROWS,
    seed: int = DEFAULT_SEED,
    *,
    include_credits: bool = False,
) -> list[dict[str, str]]:
    """Return ``rows`` synthetic Azure FOCUS 1.2 records as ordered string dicts."""
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
        elif roll < 0.45 and remaining >= 6:
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
    """Serialise the generated rows to deterministic UTF-8 CSV bytes (LF line endings)."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(COLUMNS), lineterminator="\n")
    writer.writeheader()
    for record in generate_rows(rows, seed, include_credits=include_credits):
        writer.writerow(record)
    return buffer.getvalue().encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic Azure FOCUS 1.2 CSV data.")
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS, help="number of data rows")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="deterministic RNG seed")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output CSV path")
    parser.add_argument(
        "--include-credits",
        action="store_true",
        help="emit some Credit rows with negative BilledCost (excluded from the default fixture)",
    )
    args = parser.parse_args(argv)

    payload = generate_csv_bytes(args.rows, args.seed, include_credits=args.include_credits)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(payload)
    print(f"Wrote {args.rows} rows x {len(COLUMNS)} Azure FOCUS 1.2 columns -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
