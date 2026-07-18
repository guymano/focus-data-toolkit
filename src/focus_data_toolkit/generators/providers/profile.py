"""Dataclasses describing a cloud provider's synthetic-data profile.

These hold only what is genuinely provider-specific. The engine reads the tables and calls
the callables in a fixed order, so each callable owns its own RNG draw count (which is how
per-provider byte output is reproduced despite differing id widths/alphabets and — for
commitments — differing draw *order*).
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class ServiceSpec:
    """One synthetic service. ``id_fields`` carries provider-specific resource-id parts
    (AWS ``arn_kind``; Azure ``arm_type``; GCP ``api``/``collection``) opaquely — only the
    provider's ``resource_id`` callable reads them."""

    name: str
    category: str
    subcategory: str
    resource_type: str
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
    id_fields: Mapping[str, object] = field(default_factory=dict)
    sku_details: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RowContext:
    """Identity carried from ``base_row`` into a scenario (the group's billing account)."""

    billing_id: str
    sub_id: str
    sub_name: str


@dataclass(frozen=True)
class ResourceRef:
    """Everything a provider's ``resource_id`` callable might need.

    Each provider reads only the fields it uses (AWS: region + billing account; Azure:
    subscription id + name; GCP: project id), so the callable signature stays uniform and
    the callables never draw from the RNG.
    """

    spec: ServiceSpec
    region_id: str
    region_name: str
    billing_id: str
    sub_id: str
    sub_name: str
    resource_name: str


@dataclass(frozen=True)
class CommitmentModel:
    """Provider-specific commitment terms and id/sku formats.

    ``spend_based`` is drawn once by the engine (``rng.random() < 0.6``) and passed in, so
    the callables never re-draw the spend/usage split. ``commit_id_before_base_row`` captures
    the one real cross-provider *ordering* difference: AWS draws the commitment id (from the
    region) before the purchase ``base_row``; Azure/GCP draw it after, from the subscription /
    project id. The two RNG-drawing callables own their own hex widths.
    """

    commit_id_before_base_row: bool
    commit_id: Callable[[random.Random, str, str, bool], str]  # (rng, region_id, sub_id, spend_based)
    commit_resource_name: Callable[[random.Random, bool], str]  # (rng, spend_based)
    purchase_sku_id: Callable[[random.Random], str]  # (rng) -> commitment purchase SkuId
    purchase_sku_details: Callable[[bool], str]  # (spend_based) -> SkuPriceDetails JSON
    purchase_description: Callable[[str], str]  # (commit_type) -> Purchase ChargeDescription
    commit_name: Callable[[bool], str]
    commit_type: Callable[[bool], str]  # ResourceType + CommitmentDiscountType
    commit_category: Callable[[bool], str]  # "Spend" | "Usage"
    commit_unit: Callable[[bool], str]  # "USD" | "Hours"


@dataclass(frozen=True)
class ProviderProfile:
    key: str  # "aws" | "azure" | "gcp"
    provider_name: str
    publisher_name: str
    service_provider_name: str  # FOCUS 1.3 identity (used only by the 1.3 adapter)
    host_provider_name: str
    invoice_issuer_name: str
    billing_account_type: str
    sub_account_type: str
    split_allocation_description: str  # "Shared <service> host cost allocated to workload"
    tag_keys: tuple[str, str, str]  # (environment, cost-center, owner) tag key names
    invoice_id: Callable[[str], str]  # (billing_id) -> InvoiceId
    services: tuple[ServiceSpec, ...]
    regions: tuple[tuple[str, str, tuple[str, ...]], ...]
    billing_accounts: tuple[tuple[str, str], ...]
    sub_accounts: tuple[tuple[str, str], ...]
    resource_id: Callable[[ResourceRef], str]  # pure, never draws
    resource_name: Callable[[random.Random, ServiceSpec], str]  # usage/purchase resource name
    committed_resource_name: Callable[[random.Random, ServiceSpec, int], str]  # committed-usage (index k)
    sku_id: Callable[[random.Random, ServiceSpec], str]
    sku_price_id: Callable[[random.Random], str]
    allocated_resource_id: Callable[[random.Random, str, RowContext, str], str]  # (rng, region_id, ctx, workload)
    commitment: CommitmentModel

    @property
    def commitment_service(self) -> ServiceSpec:
        """The service used for commitment and split-allocation rows (services[0])."""
        return self.services[0]

    @property
    def allowed_subcategories(self) -> frozenset[str]:
        return frozenset(spec.subcategory for spec in self.services)
