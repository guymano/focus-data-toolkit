"""Microsoft Azure provider profile (FOCUS synthetic data)."""

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
        "Virtual Machines", "Compute", "Virtual Machines", "Virtual Machine",
        "Compute", "Hours", "Standard_D4s_v5 instance", Decimal("0.192"),
        Decimal("1"), Decimal("1"), "vm-", "hourly", True, True,
        id_fields={"arm_type": "Microsoft.Compute/virtualMachines"},
        sku_details={
            "InstanceType": "Standard_D4s_v5", "InstanceSeries": "Ddsv5", "CoreCount": 4,
            "MemorySize": 16, "OperatingSystem": "Linux", "x_Tenancy": "Shared",
        },
    ),
    ServiceSpec(
        "Azure Blob Storage", "Storage", "Object Storage", "Storage Account",
        "Storage", "GB-Months", "Hot LRS data stored", Decimal("0.0184"),
        Decimal("50"), Decimal("8000"), "stor", "monthly", False, False,
        id_fields={"arm_type": "Microsoft.Storage/storageAccounts"},
        sku_details={"StorageClass": "Hot", "Redundancy": "LRS"},
    ),
    ServiceSpec(
        "Azure SQL Database", "Databases", "Relational Databases", "SQL Database",
        "Database", "Hours", "General Purpose 4 vCore", Decimal("0.504"),
        Decimal("1"), Decimal("1"), "sqldb-", "hourly", False, False,
        id_fields={"arm_type": "Microsoft.Sql/servers/databases"},
        sku_details={"InstanceType": "GP_Gen5_4", "CoreCount": 4, "MemorySize": 20, "x_Engine": "SQLServer"},
    ),
    ServiceSpec(
        "Azure Functions", "Compute", "Serverless Compute", "Function App",
        "Compute", "GB-Seconds", "Function execution duration", Decimal("0.000016"),
        Decimal("100000"), Decimal("4000000"), "func-", "hourly", False, False,
        id_fields={"arm_type": "Microsoft.Web/sites"},
        sku_details={"x_Plan": "Consumption", "x_Runtime": "dotnet8"},
    ),
    ServiceSpec(
        "Azure Kubernetes Service", "Compute", "Containers", "Managed Cluster",
        "Compute", "Hours", "AKS node pool hours", Decimal("0.192"),
        Decimal("1"), Decimal("1"), "aks-", "hourly", False, False,
        id_fields={"arm_type": "Microsoft.ContainerService/managedClusters"},
        sku_details={
            "InstanceType": "Standard_D4s_v5", "CoreCount": 4, "MemorySize": 16,
            "x_NodePool": "system",
        },
    ),
    ServiceSpec(
        "Azure Monitor", "Management and Governance", "Observability", "Log Analytics Workspace",
        "Monitoring", "GB", "Log data ingested", Decimal("2.30"),
        Decimal("1"), Decimal("500"), "law-", "daily", False, False,
        id_fields={"arm_type": "Microsoft.OperationalInsights/workspaces"},
        sku_details={"x_DataType": "Logs"},
    ),
    ServiceSpec(
        "Azure Cosmos DB", "Databases", "NoSQL Databases", "Cosmos DB Account",
        "Database", "Hours", "Provisioned throughput", Decimal("0.008"),
        Decimal("1"), Decimal("1"), "cosmos-", "hourly", False, False,
        id_fields={"arm_type": "Microsoft.DocumentDB/databaseAccounts"},
        sku_details={"x_CapacityMode": "Provisioned"},
    ),
    ServiceSpec(
        "Virtual Network", "Networking", "Network Connectivity", "Virtual Network",
        "Data Transfer", "GB", "VNet peering data transfer", Decimal("0.010"),
        Decimal("1"), Decimal("5000"), "vnet-", "daily", False, False,
        id_fields={"arm_type": "Microsoft.Network/virtualNetworks"},
        sku_details={"x_TransferType": "Peering"},
    ),
)

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


def _arm_id(ref: ResourceRef) -> str:
    return (
        f"/subscriptions/{ref.sub_id}/resourceGroups/rg-{ref.sub_name}"
        f"/providers/{ref.spec.id_fields['arm_type']}/{ref.resource_name}"
    )


def _resource_name(rng: random.Random, spec: ServiceSpec) -> str:
    return f"{spec.name_prefix}{hexid(rng, 8)}"


def _committed_resource_name(rng: random.Random, spec: ServiceSpec, k: int) -> str:
    return f"{spec.name_prefix}{k:04d}{hexid(rng, 6)}"


def _sku_id(rng: random.Random, spec: ServiceSpec) -> str:
    return f"AZ-{spec.name[:4].upper().strip()}-{hexid(rng, 6)}"


def _sku_price_id(rng: random.Random) -> str:
    return f"AZSP-{hexid(rng, 8)}"


def _allocated_resource_id(rng: random.Random, region_id: str, ctx: RowContext, workload: str) -> str:
    return (
        f"/subscriptions/{ctx.sub_id}/resourceGroups/rg-{ctx.sub_name}"
        f"/providers/Microsoft.ContainerService/managedClusters/aks-{hexid(rng, 6)}"
        f"/workloads/{workload}"
    )


def _commit_id(rng: random.Random, region_id: str, sub_id: str, spend_based: bool) -> str:
    commit_kind = "savingsPlans" if spend_based else "reservations"
    return (
        f"/subscriptions/{sub_id}/providers/Microsoft.BillingBenefits"
        f"/{commit_kind}/{hexid(rng, 12)}"
    )


def _commit_resource_name(rng: random.Random, spend_based: bool) -> str:
    commit_kind = "savingsPlans" if spend_based else "reservations"
    return f"{commit_kind}-{hexid(rng, 10)}"


def _purchase_sku_id(rng: random.Random) -> str:
    return f"AZ-COMMIT-{hexid(rng, 6)}"


def _purchase_sku_details(spend_based: bool) -> str:
    return json.dumps({"x_Term": "P1Y", "x_PaymentOption": "AllUpfront"}, separators=(",", ":"))


AZURE = ProviderProfile(
    key="azure",
    provider_name="Microsoft Azure",
    publisher_name="Microsoft",
    service_provider_name="Microsoft Azure",
    host_provider_name="Microsoft Azure",
    invoice_issuer_name="Microsoft Azure",
    billing_account_type="Microsoft Customer Agreement",
    sub_account_type="Subscription",
    split_allocation_description="Shared VM host cost allocated to workload",
    tag_keys=("Environment", "CostCenter", "Owner"),
    invoice_id=lambda billing_id: f"INV-2026-05-{billing_id[-6:]}",
    services=_SERVICES,
    regions=_REGIONS,
    billing_accounts=_BILLING_ACCOUNTS,
    sub_accounts=_SUB_ACCOUNTS,
    resource_id=_arm_id,
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
        purchase_description=lambda commit_type: f"{commit_type} commitment purchase (all upfront)",
        commit_name=lambda spend_based: (
            "AzureSavingsPlan-1yr-AllUpfront" if spend_based else "AzureReservation-1yr-AllUpfront"
        ),
        commit_type=lambda spend_based: "Savings Plan" if spend_based else "Reservation",
        commit_category=lambda spend_based: "Spend" if spend_based else "Usage",
        commit_unit=lambda spend_based: "USD" if spend_based else "Hours",
    ),
)
