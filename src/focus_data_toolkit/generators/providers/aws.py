"""AWS provider profile (FOCUS synthetic data)."""

from __future__ import annotations

import json
import random
from decimal import Decimal

from focus_data_toolkit.generators.engine.context import ResourceRef, RowContext
from focus_data_toolkit.generators.engine.determinism import hexid
from focus_data_toolkit.generators.providers.profile import (
    CommitmentModel,
    ProviderProfile,
    ServiceSpec,
)

_SERVICES: tuple[ServiceSpec, ...] = (
    ServiceSpec(
        "AmazonEC2", "Compute", "Virtual Machines", "EC2 Instance",
        "Compute", "Hours", "Linux on-demand m6i.large", Decimal("0.096"),
        Decimal("1"), Decimal("1"), "i-", "hourly", True, True,
        id_fields={"arn_kind": "instance"},
        sku_details={
            "InstanceType": "m6i.large", "InstanceSeries": "M6i", "CoreCount": 2,
            "MemorySize": 8, "OperatingSystem": "Linux", "x_Tenancy": "Shared",
        },
    ),
    ServiceSpec(
        "AmazonS3", "Storage", "Object Storage", "S3 Bucket",
        "Storage", "GB-Months", "S3 Standard storage", Decimal("0.023"),
        Decimal("50"), Decimal("8000"), "bucket-", "monthly", False, False,
        id_fields={"arn_kind": "bucket"},
        sku_details={"StorageClass": "Standard", "Redundancy": "LRS"},
    ),
    ServiceSpec(
        "AmazonRDS", "Databases", "Relational Databases", "RDS Instance",
        "Database", "Hours", "RDS PostgreSQL db.r6g.large", Decimal("0.240"),
        Decimal("1"), Decimal("1"), "db-", "hourly", False, False,
        id_fields={"arn_kind": "db"},
        sku_details={
            "InstanceType": "db.r6g.large", "InstanceSeries": "R6g", "CoreCount": 2,
            "MemorySize": 16, "x_Engine": "PostgreSQL",
        },
    ),
    ServiceSpec(
        "AWSLambda", "Compute", "Serverless Compute", "Lambda Function",
        "Compute", "GB-Seconds", "Lambda function duration", Decimal("0.0000166667"),
        Decimal("100000"), Decimal("5000000"), "fn-", "hourly", False, False,
        id_fields={"arn_kind": "function"},
        sku_details={"x_Runtime": "python3.12", "x_Architecture": "arm64"},
    ),
    ServiceSpec(
        "AmazonVPC", "Networking", "Network Connectivity", "NAT Gateway",
        "Data Transfer", "GB", "NAT gateway data processed", Decimal("0.045"),
        Decimal("1"), Decimal("500"), "nat-", "daily", False, False,
        id_fields={"arn_kind": "natgateway"},
        sku_details={"x_TransferType": "InterAZ"},
    ),
    ServiceSpec(
        "AmazonCloudWatch", "Management and Governance", "Observability", "Metric",
        "Monitoring", "Metrics", "Custom metrics", Decimal("0.300"),
        Decimal("1"), Decimal("200"), "metric-", "daily", False, False,
        id_fields={"arn_kind": "metric"},
        sku_details={"x_MetricType": "Custom"},
    ),
    ServiceSpec(
        "AmazonDynamoDB", "Databases", "NoSQL Databases", "DynamoDB Table",
        "Database", "Requests", "DynamoDB on-demand write requests", Decimal("0.00000125"),
        Decimal("100000"), Decimal("5000000"), "table-", "daily", False, False,
        id_fields={"arn_kind": "table"},
        sku_details={"x_CapacityMode": "On-Demand"},
    ),
    ServiceSpec(
        "AWSGlue", "Analytics", "Data Processing", "Glue Job",
        "Data Processing", "DPU-Hours", "Glue ETL job run", Decimal("0.440"),
        Decimal("1"), Decimal("200"), "job-", "daily", False, False,
        id_fields={"arn_kind": "job"},
        sku_details={"x_WorkerType": "G.1X"},
    ),
)

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


def _resource_id(ref: ResourceRef) -> str:
    svc = ref.spec.name[6:].lower() or "svc"
    return (
        f"arn:aws:{svc}:{ref.region_id}:{ref.billing_id}:"
        f"{ref.spec.id_fields['arn_kind']}/{ref.resource_name}"
    )


def _resource_name(rng: random.Random, spec: ServiceSpec) -> str:
    return f"{spec.name_prefix}{hexid(rng, 12)}"


def _committed_resource_name(rng: random.Random, spec: ServiceSpec, k: int) -> str:
    return f"{spec.name_prefix}{k:04d}{hexid(rng, 8)}"


def _sku_id(rng: random.Random, spec: ServiceSpec) -> str:
    return f"SKU-{spec.name[:6].upper()}-{hexid(rng, 6)}"


def _sku_price_id(rng: random.Random) -> str:
    return f"SPRICE-{hexid(rng, 8)}"


def _allocated_resource_id(rng: random.Random, region_id: str, ctx: RowContext, workload: str) -> str:
    return f"arn:aws:eks:{region_id}:{ctx.billing_id}:workload/{workload}-{hexid(rng, 6)}"


def _commit_id(rng: random.Random, region_id: str, sub_id: str, spend_based: bool) -> str:
    commit_kind = "savingsplan" if spend_based else "reservation"
    service = "savingsplans" if spend_based else "ec2"
    return f"arn:aws:{service}:{region_id}::{commit_kind}/{hexid(rng, 16)}"


def _commit_resource_name(rng: random.Random, spend_based: bool) -> str:
    commit_kind = "savingsplan" if spend_based else "reservation"
    return f"{commit_kind}-{hexid(rng, 12)}"


def _purchase_sku_id(rng: random.Random) -> str:
    return f"SKU-COMMIT-{hexid(rng, 6)}"


def _purchase_sku_details(spend_based: bool) -> str:
    return json.dumps({"x_PurchaseTerm": "1yr", "x_PaymentOption": "AllUpfront"}, separators=(",", ":"))


AWS = ProviderProfile(
    key="aws",
    provider_name="AWS",
    publisher_name="AWS",
    service_provider_name="AWS",
    host_provider_name="AWS",
    invoice_issuer_name="AWS",
    billing_account_type="Payer Account",
    sub_account_type="Linked Account",
    split_allocation_description="Shared EC2 host cost allocated to workload",
    tag_keys=("Environment", "CostCenter", "Owner"),
    invoice_id=lambda billing_id: f"INV-2026-05-{billing_id[-4:]}",
    services=_SERVICES,
    regions=_REGIONS,
    billing_accounts=_BILLING_ACCOUNTS,
    sub_accounts=_SUB_ACCOUNTS,
    resource_id=_resource_id,
    resource_name=_resource_name,
    committed_resource_name=_committed_resource_name,
    sku_id=_sku_id,
    sku_price_id=_sku_price_id,
    allocated_resource_id=_allocated_resource_id,
    commitment=CommitmentModel(
        commit_id_before_base_row=True,
        commit_id=_commit_id,
        commit_resource_name=_commit_resource_name,
        purchase_sku_id=_purchase_sku_id,
        purchase_sku_details=_purchase_sku_details,
        purchase_description=lambda commit_type: f"{commit_type} commitment purchase (all upfront)",
        commit_name=lambda spend_based: (
            "ComputeSavingsPlan-1yr-AllUpfront" if spend_based else "EC2ReservedInstance-1yr-AllUpfront"
        ),
        commit_type=lambda spend_based: "Savings Plan" if spend_based else "Reserved Instance",
        commit_category=lambda spend_based: "Spend" if spend_based else "Usage",
        commit_unit=lambda spend_based: "USD" if spend_based else "Hours",
    ),
)
