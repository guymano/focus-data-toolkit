"""Deterministic generator for synthetic AWS data in the FOCUS 1.2 format.

FOCUS 1.2 (https://focus.finops.org/focus-specification/v1-2/) defines exactly
57 columns. This module emits conformant, realistic AWS cost-and-usage rows.

Realism + conformance (verified against the FOCUS v1.2 spec source)
------------------------------------------------------------------
* FOCUS Column IDs (`ProviderName`/`PublisherName`/`InvoiceIssuerName`,
  `RegionId`/`RegionName`).
* `UnitFormat`-compliant units ("Hours", "GB-Months", "GB-Seconds", "DPU-Hours",
  count nouns like "Metrics"/"Requests"/"Units") — not "Hrs"/"GB-Mo".
* `SkuPriceDetails` uses FOCUS-defined keys where applicable (CoreCount,
  MemorySize, InstanceType, InstanceSeries, OperatingSystem) plus `x_` keys.
* Realistic commitment model (per the FOCUS sample datasets): a Purchase row for
  the commitment plus committed Usage rows that share its `CommitmentDiscountId`,
  with `BilledCost`=0 and `EffectiveCost` < `ListCost` (amortised rate).
* Per-column conditionality honoured: PricingCategory in {Standard, Committed}
  (null for Tax); SkuId/SkuPriceId null for Tax; ConsumedQuantity null unless
  Usage with status != Unused; AvailabilityZone only for zonal (compute) rows;
  ChargeClass null; spend-based commitments denominated in currency.
* Compute/serverless billed hourly, storage monthly, the rest daily.

Synthetic / PII-free, deterministic (seeded RNG + fixed timestamps -> a
given (rows, seed) is byte-reproducible). Self-contained (standard library
only).

CLI
---
    python -m focus_data_toolkit.generators.generate_aws_focus_1_2 \
        --rows 1000 --seed 1202 \
        --out focus_sample_costandusage_aws_1000.csv
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
# FOCUS v1.2 FOCUS-defined SkuPriceDetails property keys (others MUST be x_-prefixed).
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
DEFAULT_OUT = Path("focus_sample_costandusage_aws_1000.csv")
PROVIDER_NAME = "AWS"
PUBLISHER_NAME = "AWS"
INVOICE_ISSUER_NAME = "AWS"

_BILLING_START = datetime(2026, 5, 1, tzinfo=UTC)
_BILLING_END = datetime(2026, 6, 1, tzinfo=UTC)
_PERIOD_DAYS = 28
_PERIOD_HOURS = _PERIOD_DAYS * 24

_COST_Q = Decimal("0.000001")
_PRICE_Q = Decimal("0.0000000001")
_QTY_Q = Decimal("0.0001")
_EUR_PER_USD = Decimal("0.92")
_COMMIT_RATE = Decimal("0.667")  # amortised commitment rate vs on-demand list
_PRIVATE_RATE = Decimal("0.90")  # negotiated (contracted) rate vs list for on-demand
_COMMIT_TERM_HOURS = Decimal("8760")  # 1-year reservation term


@dataclass(frozen=True)
class _ServiceSpec:
    name: str
    category: str
    subcategory: str
    resource_type: str
    arn_kind: str
    sku_meter: str  # the SKU "Function" (Compute / Storage / Database / ...)
    pricing_unit: str  # FOCUS UnitFormat
    description: str
    unit_price_usd: Decimal
    qty_low: Decimal
    qty_high: Decimal
    name_prefix: str
    granularity: str  # "hourly" | "daily" | "monthly"
    zonal: bool
    commitment_eligible: bool
    sku_details: dict[str, object] = field(default_factory=dict)


_SERVICES: tuple[_ServiceSpec, ...] = (
    _ServiceSpec(
        "AmazonEC2", "Compute", "Virtual Machines", "EC2 Instance", "instance",
        "Compute", "Hours", "Linux on-demand m6i.large", Decimal("0.096"),
        Decimal("1"), Decimal("1"), "i-", "hourly", True, True,
        {
            "InstanceType": "m6i.large", "InstanceSeries": "M6i", "CoreCount": 2,
            "MemorySize": 8, "OperatingSystem": "Linux", "x_Tenancy": "Shared",
        },
    ),
    _ServiceSpec(
        "AmazonS3", "Storage", "Object Storage", "S3 Bucket", "bucket",
        "Storage", "GB-Months", "S3 Standard storage", Decimal("0.023"),
        Decimal("50"), Decimal("8000"), "bucket-", "monthly", False, False,
        {"x_StorageClass": "Standard", "x_Redundancy": "LRS"},
    ),
    _ServiceSpec(
        "AmazonRDS", "Databases", "Relational Databases", "RDS Instance", "db",
        "Database", "Hours", "RDS PostgreSQL db.r6g.large", Decimal("0.240"),
        Decimal("1"), Decimal("1"), "db-", "hourly", False, False,
        {
            "InstanceType": "db.r6g.large", "InstanceSeries": "R6g", "CoreCount": 2,
            "MemorySize": 16, "x_Engine": "PostgreSQL",
        },
    ),
    _ServiceSpec(
        "AWSLambda", "Compute", "Serverless Compute", "Lambda Function", "function",
        "Compute", "GB-Seconds", "Lambda function duration", Decimal("0.0000166667"),
        Decimal("100000"), Decimal("5000000"), "fn-", "hourly", False, False,
        {"x_Runtime": "python3.12", "x_Architecture": "arm64"},
    ),
    _ServiceSpec(
        "AmazonVPC", "Networking", "Network Connectivity", "NAT Gateway", "natgateway",
        "Data Transfer", "GB", "NAT gateway data processed", Decimal("0.045"),
        Decimal("1"), Decimal("500"), "nat-", "daily", False, False,
        {"x_TransferType": "InterAZ"},
    ),
    _ServiceSpec(
        "AmazonCloudWatch", "Management and Governance", "Observability", "Metric", "metric",
        "Monitoring", "Metrics", "Custom metrics", Decimal("0.300"),
        Decimal("1"), Decimal("200"), "metric-", "daily", False, False,
        {"x_MetricType": "Custom"},
    ),
    _ServiceSpec(
        "AmazonDynamoDB", "Databases", "NoSQL Databases", "DynamoDB Table", "table",
        "Database", "Requests", "DynamoDB on-demand write requests", Decimal("0.00000125"),
        Decimal("100000"), Decimal("5000000"), "table-", "daily", False, False,
        {"x_CapacityMode": "On-Demand"},
    ),
    _ServiceSpec(
        "AWSGlue", "Analytics", "Data Processing", "Glue Job", "job",
        "Data Processing", "DPU-Hours", "Glue ETL job run", Decimal("0.440"),
        Decimal("1"), Decimal("200"), "job-", "daily", False, False,
        {"x_WorkerType": "G.1X"},
    ),
)
_EC2 = _SERVICES[0]
ALLOWED_SUBCATEGORIES: frozenset[str] = frozenset(s.subcategory for s in _SERVICES)

_REGIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("us-east-1", "US East (N. Virginia)", ("us-east-1a", "us-east-1b", "us-east-1c")),
    ("us-west-2", "US West (Oregon)", ("us-west-2a", "us-west-2b")),
    ("eu-west-1", "EU (Ireland)", ("eu-west-1a", "eu-west-1b")),
    ("ap-southeast-1", "Asia Pacific (Singapore)", ("ap-southeast-1a", "ap-southeast-1b")),
)

_BILLING_ACCOUNTS: tuple[tuple[str, str], ...] = (
    ("100000000001", "ExampleCorp Master Payer"),
    ("100000000002", "ExampleCorp Secondary Payer"),
)

_SUB_ACCOUNTS: tuple[tuple[str, str], ...] = (
    ("200000000011", "prod-platform"),
    ("200000000012", "staging-platform"),
    ("200000000013", "data-analytics"),
    ("200000000014", "sandbox-dev"),
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


def _sku_price_details(rng: random.Random, spec: _ServiceSpec) -> str:
    return json.dumps(spec.sku_details, separators=(",", ":"))


def _resource_id(
    rng: random.Random, spec: _ServiceSpec, region: str, account: str, name: str
) -> str:
    svc = spec.name[6:].lower() or "svc"
    return f"arn:aws:{svc}:{region}:{account}:{spec.arn_kind}/{name}"


def _base_row(rng: random.Random) -> tuple[dict[str, str], dict[str, str]]:
    """Return (row, ctx) with identity/account/period-independent fields filled."""
    billing_id, billing_name = rng.choice(_BILLING_ACCOUNTS)
    sub_id, sub_name = rng.choice(_SUB_ACCOUNTS)
    row = {name: "" for name in COLUMNS}
    row["ProviderName"] = PROVIDER_NAME
    row["PublisherName"] = PUBLISHER_NAME
    row["InvoiceIssuerName"] = INVOICE_ISSUER_NAME
    row["InvoiceId"] = f"INV-2026-05-{billing_id[-4:]}"
    row["BillingAccountId"] = billing_id
    row["BillingAccountName"] = billing_name
    row["BillingAccountType"] = "Payer Account"
    row["SubAccountId"] = sub_id
    row["SubAccountName"] = sub_name
    row["SubAccountType"] = "Linked Account"
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
    row["ResourceId"] = _resource_id(rng, spec, region_id, ctx["billing_id"], resource_name)
    row["ResourceName"] = resource_name
    row["ResourceType"] = spec.resource_type
    row["SkuId"] = f"SKU-{spec.name[:6].upper()}-{_hexid(rng, 6)}"
    row["SkuMeter"] = spec.sku_meter
    row["SkuPriceId"] = f"SPRICE-{_hexid(rng, 8)}"
    row["SkuPriceDetails"] = _sku_price_details(rng, spec)


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
    start, end = _period(i, spec.granularity)
    row["ChargePeriodStart"], row["ChargePeriodEnd"] = start, end
    _set_service(row, spec)
    resource_name = f"{spec.name_prefix}{_hexid(rng, 12)}"
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
    resource_name = f"{spec.name_prefix}{_hexid(rng, 12)}"
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
    # ConsumedQuantity/Unit stay null (not a Usage charge).
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
    # PricingCategory, Sku*, Resource*, Consumed*, AvailabilityZone stay null for Tax.
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
    """A commitment Purchase row + linked committed-usage rows (shared CommitmentDiscountId)."""
    spec = _EC2
    region_id, region_name, azs = rng.choice(_REGIONS)
    az = rng.choice(azs)
    spend_based = rng.random() < 0.6
    commit_kind = "savingsplan" if spend_based else "reservation"
    commit_id = (
        f"arn:aws:{'savingsplans' if spend_based else 'ec2'}:{region_id}::"
        f"{commit_kind}/{_hexid(rng, 16)}"
    )
    commit_name = (
        "ComputeSavingsPlan-1yr-AllUpfront" if spend_based else "EC2ReservedInstance-1yr-AllUpfront"
    )
    commit_type = "Savings Plan" if spend_based else "Reserved Instance"
    commit_category = "Spend" if spend_based else "Usage"
    commit_unit = "USD" if spend_based else "Hours"

    list_unit = _q(spec.unit_price_usd, _PRICE_Q)
    commit_unit_price = _q(list_unit * _COMMIT_RATE, _PRICE_Q)
    upfront = _q(commit_unit_price * _COMMIT_TERM_HOURS, _COST_Q)
    commit_total_qty = _s(upfront) if spend_based else _s(_COMMIT_TERM_HOURS)

    # Purchase row (the commitment itself).
    purchase, ctx = _base_row(rng)
    purchase["ChargePeriodStart"] = _iso(_BILLING_START)
    purchase["ChargePeriodEnd"] = _iso(_BILLING_START + timedelta(hours=1))
    _set_service(purchase, spec)
    commit_resource = f"{commit_kind}-{_hexid(rng, 12)}"
    purchase["ResourceId"] = commit_id
    purchase["ResourceName"] = commit_resource
    purchase["ResourceType"] = commit_type
    purchase["RegionId"] = region_id
    purchase["RegionName"] = region_name
    purchase["SkuId"] = f"SKU-COMMIT-{_hexid(rng, 6)}"
    purchase["SkuMeter"] = "Commitment"
    purchase["SkuPriceId"] = f"SPRICE-{_hexid(rng, 8)}"
    purchase["SkuPriceDetails"] = json.dumps(
        {"x_PurchaseTerm": "1yr", "x_PaymentOption": "AllUpfront"}, separators=(",", ":")
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
        # Reuse the group's account so the commitment and usage are consistent.
        usage["BillingAccountId"] = ctx["billing_id"]
        usage["SubAccountId"] = ctx["sub_id"]
        usage["SubAccountName"] = ctx["sub_name"]
        start, end = _period(i0 + 1 + k, "hourly")
        usage["ChargePeriodStart"], usage["ChargePeriodEnd"] = start, end
        _set_service(usage, spec)
        resource_name = f"{spec.name_prefix}{k:04d}{_hexid(rng, 8)}"
        usage["RegionId"] = region_id
        usage["RegionName"] = region_name
        usage["ResourceId"] = _resource_id(rng, spec, region_id, ctx["billing_id"], resource_name)
        usage["ResourceName"] = resource_name
        usage["ResourceType"] = spec.resource_type
        usage["AvailabilityZone"] = az
        usage["SkuId"] = f"SKU-{spec.name[:6].upper()}-{_hexid(rng, 6)}"
        usage["SkuMeter"] = spec.sku_meter
        usage["SkuPriceId"] = f"SPRICE-{_hexid(rng, 8)}"
        usage["SkuPriceDetails"] = _sku_price_details(rng, spec)
        list_cost = _q(list_unit, _COST_Q)
        effective = _q(commit_unit_price, _COST_Q)
        usage["ChargeCategory"] = "Usage"
        usage["ChargeFrequency"] = "Usage-Based"
        usage["ChargeDescription"] = f"{spec.name} committed usage"
        usage["PricingCategory"] = "Committed"
        usage["BilledCost"] = "0.000000"  # covered by the upfront purchase
        usage["EffectiveCost"] = _s(effective)  # amortised, < ListCost
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
    """Return ``rows`` synthetic FOCUS 1.2 records as ordered string dicts."""
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
    parser = argparse.ArgumentParser(description="Generate synthetic AWS FOCUS 1.2 CSV data.")
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
    print(f"Wrote {args.rows} rows x {len(COLUMNS)} FOCUS 1.2 columns -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
