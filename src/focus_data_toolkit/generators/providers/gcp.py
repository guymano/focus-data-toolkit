"""Google Cloud provider profile (FOCUS synthetic data)."""

from __future__ import annotations

import json
import random
from decimal import Decimal

from focus_data_toolkit.generators.engine.context import ResourceRef, RowContext
from focus_data_toolkit.generators.engine.determinism import HEX_UPPER, hexid
from focus_data_toolkit.generators.providers.profile import (
    CommitmentModel,
    ProviderProfile,
    ServiceSpec,
)

_SERVICES: tuple[ServiceSpec, ...] = (
    ServiceSpec(
        "Compute Engine", "Compute", "Virtual Machines", "Compute Instance",
        "Compute", "Hours", "e2-standard-4 running", Decimal("0.134"),
        Decimal("1"), Decimal("1"), "instance-", "hourly", True, True,
        id_fields={"api": "compute.googleapis.com", "collection": "instances"},
        sku_details={
            "InstanceType": "e2-standard-4", "InstanceSeries": "E2", "CoreCount": 4,
            "MemorySize": 16, "OperatingSystem": "Linux", "x_Preemptible": "false",
        },
    ),
    ServiceSpec(
        "Cloud Storage", "Storage", "Object Storage", "Storage Bucket",
        "Storage", "GB-Months", "Standard storage", Decimal("0.020"),
        Decimal("50"), Decimal("8000"), "bucket-", "monthly", False, False,
        id_fields={"api": "storage.googleapis.com", "collection": "buckets"},
        sku_details={"StorageClass": "Standard"},
    ),
    ServiceSpec(
        "Cloud SQL", "Databases", "Relational Databases", "Cloud SQL Instance",
        "Database", "Hours", "Cloud SQL custom 4 vCPU", Decimal("0.200"),
        Decimal("1"), Decimal("1"), "sql-", "hourly", False, False,
        id_fields={"api": "sqladmin.googleapis.com", "collection": "instances"},
        sku_details={"InstanceType": "db-custom-4-16384", "CoreCount": 4, "MemorySize": 16,
                     "x_Engine": "PostgreSQL"},
    ),
    ServiceSpec(
        "Cloud Functions", "Compute", "Serverless Compute", "Cloud Function",
        "Compute", "GB-Seconds", "Function execution duration", Decimal("0.0000025"),
        Decimal("100000"), Decimal("8000000"), "fn-", "hourly", False, False,
        id_fields={"api": "cloudfunctions.googleapis.com", "collection": "functions"},
        sku_details={"x_Runtime": "python312", "x_Generation": "gen2"},
    ),
    ServiceSpec(
        "Google Kubernetes Engine", "Compute", "Containers", "GKE Cluster",
        "Compute", "Hours", "GKE node pool hours", Decimal("0.134"),
        Decimal("1"), Decimal("1"), "gke-", "hourly", False, False,
        id_fields={"api": "container.googleapis.com", "collection": "clusters"},
        sku_details={
            "InstanceType": "e2-standard-4", "CoreCount": 4, "MemorySize": 16,
            "x_NodePool": "default",
        },
    ),
    ServiceSpec(
        "Cloud Monitoring", "Management and Governance", "Observability", "Monitoring Workspace",
        "Monitoring", "MiB", "Monitoring data ingested", Decimal("0.2580"),
        Decimal("1"), Decimal("300"), "mon-", "daily", False, False,
        id_fields={"api": "monitoring.googleapis.com", "collection": "metricsScopes"},
        sku_details={"x_DataType": "Metrics"},
    ),
    ServiceSpec(
        "BigQuery", "Databases", "Data Warehouses", "BigQuery Dataset",
        "Analysis", "TiB", "On-demand query analysis", Decimal("6.250"),
        Decimal("0.01"), Decimal("50"), "ds_", "daily", False, False,
        id_fields={"api": "bigquery.googleapis.com", "collection": "datasets"},
        sku_details={"x_Edition": "OnDemand"},
    ),
    ServiceSpec(
        "Cloud Run", "Compute", "Serverless Compute", "Cloud Run Service",
        "Compute", "Core-Seconds", "Cloud Run vCPU allocation", Decimal("0.000024"),
        Decimal("100000"), Decimal("5000000"), "svc-", "hourly", False, False,
        id_fields={"api": "run.googleapis.com", "collection": "services"},
        sku_details={"x_Generation": "gen2"},
    ),
)

_REGIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("us-central1", "Iowa", ("us-central1-a", "us-central1-b", "us-central1-c")),
    ("europe-west1", "Belgium", ("europe-west1-b", "europe-west1-c")),
    ("us-east1", "South Carolina", ("us-east1-b", "us-east1-c")),
    ("asia-southeast1", "Singapore", ("asia-southeast1-a", "asia-southeast1-b")),
)

_BILLING_ACCOUNTS: tuple[tuple[str, str], ...] = (
    ("01A2B3-C4D5E6-F70819", "ExampleCorp Billing - Primary"),
    ("11C2D3-E4F506-A7B8C9", "ExampleCorp Billing - Secondary"),
)

_SUB_ACCOUNTS: tuple[tuple[str, str], ...] = (
    ("examplecorp-prod-481627", "prod-platform"),
    ("examplecorp-staging-552831", "staging-platform"),
    ("examplecorp-data-739104", "data-analytics"),
    ("examplecorp-sandbox-160942", "sandbox-dev"),
)


def _sku(rng: random.Random) -> str:
    def block() -> str:
        return hexid(rng, 4, HEX_UPPER)

    return f"{block()}-{block()}-{block()}"


def _resource_id(ref: ResourceRef) -> str:
    return (
        f"//{ref.spec.id_fields['api']}/projects/{ref.sub_id}"
        f"/{ref.spec.id_fields['collection']}/{ref.resource_name}"
    )


def _resource_name(rng: random.Random, spec: ServiceSpec) -> str:
    return f"{spec.name_prefix}{hexid(rng, 8)}"


def _committed_resource_name(rng: random.Random, spec: ServiceSpec, k: int) -> str:
    return f"{spec.name_prefix}{k:04d}{hexid(rng, 6)}"


def _sku_id(rng: random.Random, spec: ServiceSpec) -> str:
    return _sku(rng)


def _sku_price_id(rng: random.Random) -> str:
    return _sku(rng)


def _allocated_resource_id(rng: random.Random, region_id: str, ctx: RowContext, workload: str) -> str:
    return (
        f"//container.googleapis.com/projects/{ctx.sub_id}/clusters/gke-{hexid(rng, 6)}"
        f"/workloads/{workload}"
    )


def _commit_id(rng: random.Random, region_id: str, sub_id: str, spend_based: bool) -> str:
    return (
        f"//compute.googleapis.com/projects/{sub_id}/regions/{region_id}"
        f"/commitments/cud-{hexid(rng, 10)}"
    )


def _commit_resource_name(rng: random.Random, spend_based: bool) -> str:
    return f"cud-{hexid(rng, 10)}"


def _purchase_sku_id(rng: random.Random) -> str:
    return _sku(rng)


def _purchase_sku_details(spend_based: bool) -> str:
    return json.dumps({"x_Plan": "TWELVE_MONTH", "x_Type": "COMPUTE_OPTIMIZED"}, separators=(",", ":"))


GCP = ProviderProfile(
    key="gcp",
    provider_name="Google Cloud",
    publisher_name="Google",
    service_provider_name="Google Cloud",
    host_provider_name="Google Cloud",
    invoice_issuer_name="Google Cloud",
    billing_account_type="Billing Account",
    sub_account_type="Project",
    split_allocation_description="Shared Compute Engine host cost allocated to workload",
    tag_keys=("environment", "cost-center", "owner"),
    invoice_id=lambda billing_id: f"INV-2026-05-{billing_id.replace('-', '')[-6:]}",
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
        commit_id_before_base_row=False,
        commit_id=_commit_id,
        commit_resource_name=_commit_resource_name,
        purchase_sku_id=_purchase_sku_id,
        purchase_sku_details=_purchase_sku_details,
        purchase_description=lambda commit_type: "Committed Use Discount purchase (all upfront)",
        commit_name=lambda spend_based: "SpendBasedCUD-1yr" if spend_based else "ResourceBasedCUD-1yr-Compute",
        commit_type=lambda spend_based: "Committed Use Discount",
        commit_category=lambda spend_based: "Spend" if spend_based else "Usage",
        commit_unit=lambda spend_based: "USD" if spend_based else "Hours",
    ),
)
