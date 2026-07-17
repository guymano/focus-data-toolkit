"""Provider- and version-agnostic FOCUS row builders.

One implementation per scenario (usage / purchase / tax / credit / split-allocation /
commitment), parameterised by a ``ProviderProfile`` and a ``VersionAdapter``. The historical
per-provider RNG draw order is preserved exactly: the shared skeleton draws in the same order,
and each provider callable owns its own draw count/alphabet. Provider constants live in the
profile, version deltas in the adapter — no FOCUS rule is implemented here more than once.
"""

from __future__ import annotations

import json
import random
from datetime import timedelta
from decimal import Decimal

from focus_data_toolkit.generators.engine.context import ResourceRef, RowContext
from focus_data_toolkit.generators.engine.determinism import (
    BILLING_END,
    BILLING_START,
    COMMIT_RATE,
    COMMIT_TERM_HOURS,
    COST_CENTERS,
    COST_Q,
    ENVIRONMENTS,
    OWNERS,
    PRICE_Q,
    PRIVATE_RATE,
    QTY_Q,
    contract_id_for,
    hexid,
    iso,
    period,
    q,
    s,
    set_currency,
    sku_price_details,
)
from focus_data_toolkit.generators.engine.json_focus import allocated_method_details

# Split Cost Allocation vocabularies (FOCUS 1.3; identical across providers).
ALLOCATION_METHODS: tuple[tuple[str, dict[str, object]], ...] = (
    ("split-proportional", {"x_Strategy": "Proportional", "x_Basis": "vCPUSeconds"}),
    ("split-even", {"x_Strategy": "Even", "x_Basis": "Workloads"}),
    ("split-weighted", {"x_Strategy": "Weighted", "x_Basis": "MemoryBytes"}),
)
ALLOCATION_WORKLOADS = ("checkout", "search", "billing", "analytics", "ingestion")


def base_row(rng: random.Random, profile, adapter) -> tuple[dict[str, str], RowContext]:
    """Return (row, ctx) with identity/account/period-independent fields filled."""
    billing_id, billing_name = rng.choice(profile.billing_accounts)
    sub_id, sub_name = rng.choice(profile.sub_accounts)
    row = {name: "" for name in adapter.columns}
    row["ProviderName"] = profile.provider_name
    row["PublisherName"] = profile.publisher_name
    row["InvoiceIssuerName"] = profile.invoice_issuer_name
    row["InvoiceId"] = profile.invoice_id(billing_id)
    row["BillingAccountId"] = billing_id
    row["BillingAccountName"] = billing_name
    row["BillingAccountType"] = profile.billing_account_type
    row["SubAccountId"] = sub_id
    row["SubAccountName"] = sub_name
    row["SubAccountType"] = profile.sub_account_type
    row["BillingPeriodStart"] = iso(BILLING_START)
    row["BillingPeriodEnd"] = iso(BILLING_END)
    row["BillingCurrency"] = "USD"
    adapter.fill_version_identity(row, profile)
    env_key, cost_center_key, owner_key = profile.tag_keys
    row["Tags"] = json.dumps(
        {
            env_key: rng.choice(ENVIRONMENTS),
            cost_center_key: rng.choice(COST_CENTERS),
            owner_key: rng.choice(OWNERS),
        },
        separators=(",", ":"),
    )
    return row, RowContext(billing_id=billing_id, sub_id=sub_id, sub_name=sub_name)


def _set_service(row: dict[str, str], spec) -> None:
    row["ServiceName"] = spec.name
    row["ServiceCategory"] = spec.category
    row["ServiceSubcategory"] = spec.subcategory


def _set_resource_sku(
    rng: random.Random, row: dict[str, str], spec, ctx: RowContext,
    region_id: str, region_name: str, resource_name: str, profile,
) -> None:
    row["RegionId"] = region_id
    row["RegionName"] = region_name
    ref = ResourceRef(
        spec=spec, region_id=region_id, region_name=region_name,
        billing_id=ctx.billing_id, sub_id=ctx.sub_id, sub_name=ctx.sub_name,
        resource_name=resource_name,
    )
    row["ResourceId"] = profile.resource_id(ref)
    row["ResourceName"] = resource_name
    row["ResourceType"] = spec.resource_type
    row["SkuId"] = profile.sku_id(rng, spec)
    row["SkuMeter"] = spec.sku_meter
    row["SkuPriceId"] = profile.sku_price_id(rng)
    row["SkuPriceDetails"] = sku_price_details(dict(spec.sku_details))


def usage_row(rng: random.Random, i: int, remaining: int, profile, adapter) -> dict[str, str]:
    spec = rng.choice(profile.services)
    region_id, region_name, azs = rng.choice(profile.regions)
    row, ctx = base_row(rng, profile, adapter)
    row["ChargePeriodStart"], row["ChargePeriodEnd"] = period(i, spec.granularity)
    _set_service(row, spec)
    resource_name = profile.resource_name(rng, spec)
    _set_resource_sku(rng, row, spec, ctx, region_id, region_name, resource_name, profile)
    if spec.zonal:
        row["AvailabilityZone"] = rng.choice(azs)

    quantity = q(Decimal(rng.uniform(float(spec.qty_low), float(spec.qty_high))), QTY_Q)
    jitter = Decimal(rng.uniform(0.97, 1.03))
    list_unit = q(spec.unit_price_usd * jitter, PRICE_Q)
    contracted_unit = q(list_unit * PRIVATE_RATE, PRICE_Q)
    list_cost = q(list_unit * quantity, COST_Q)
    contracted_cost = q(contracted_unit * quantity, COST_Q)

    row["ChargeCategory"] = "Usage"
    row["ChargeFrequency"] = "Usage-Based"
    row["ChargeDescription"] = spec.description
    row["PricingCategory"] = "Standard"
    row["BilledCost"] = s(contracted_cost)
    row["EffectiveCost"] = s(contracted_cost)
    row["ListCost"] = s(list_cost)
    row["ContractedCost"] = s(contracted_cost)
    row["ListUnitPrice"] = s(list_unit)
    row["ContractedUnitPrice"] = s(contracted_unit)
    row["PricingQuantity"] = s(quantity)
    row["PricingUnit"] = spec.pricing_unit
    row["ConsumedQuantity"] = s(quantity)
    row["ConsumedUnit"] = spec.pricing_unit
    set_currency(
        row, "EUR" if rng.random() < 0.10 else "USD", list_unit, contracted_unit, contracted_cost
    )
    return row


def standalone_purchase_row(rng: random.Random, i: int, remaining: int, profile, adapter) -> dict[str, str]:
    spec = rng.choice(profile.services)
    region_id, region_name, _ = rng.choice(profile.regions)
    row, ctx = base_row(rng, profile, adapter)
    row["ChargePeriodStart"], row["ChargePeriodEnd"] = period(i, "daily")
    _set_service(row, spec)
    resource_name = profile.resource_name(rng, spec)
    _set_resource_sku(rng, row, spec, ctx, region_id, region_name, resource_name, profile)

    amount = q(Decimal(rng.uniform(20.0, 800.0)), COST_Q)
    row["ChargeCategory"] = "Purchase"
    row["ChargeFrequency"] = "Recurring"
    row["ChargeDescription"] = f"{spec.name} subscription fee"
    row["PricingCategory"] = "Standard"
    row["BilledCost"] = s(amount)
    row["EffectiveCost"] = "0"  # purchase covers future eligible charges
    row["ListCost"] = s(amount)
    row["ContractedCost"] = s(amount)
    row["ListUnitPrice"] = s(amount)
    row["ContractedUnitPrice"] = s(amount)
    row["PricingQuantity"] = "1"
    row["PricingUnit"] = "Units"
    set_currency(row, "USD", amount, amount, Decimal("0"))
    return row


def tax_row(rng: random.Random, i: int, remaining: int, profile, adapter) -> dict[str, str]:
    spec = rng.choice(profile.services)
    row, _ = base_row(rng, profile, adapter)
    row["ChargePeriodStart"], row["ChargePeriodEnd"] = period(i, "daily")
    _set_service(row, spec)
    amount = q(Decimal(rng.uniform(0.5, 50.0)), COST_Q)
    amount_str = s(amount)
    row["ChargeCategory"] = "Tax"
    row["ChargeFrequency"] = "One-Time"
    row["ChargeDescription"] = f"Tax for {spec.name}"
    row["BilledCost"] = amount_str
    row["EffectiveCost"] = amount_str
    row["ListCost"] = amount_str
    row["ContractedCost"] = amount_str
    adapter.on_tax_row(row, amount_str)
    return row


def credit_row(rng: random.Random, i: int, remaining: int, profile, adapter) -> dict[str, str]:
    spec = rng.choice(profile.services)
    row, _ = base_row(rng, profile, adapter)
    row["ChargePeriodStart"], row["ChargePeriodEnd"] = period(i, "daily")
    _set_service(row, spec)
    negative = s(-q(Decimal(rng.uniform(1.0, 100.0)), COST_Q))
    row["ChargeCategory"] = "Credit"
    row["ChargeFrequency"] = "One-Time"
    row["ChargeDescription"] = f"Credit for {spec.name}"
    row["BilledCost"] = negative
    row["EffectiveCost"] = negative
    row["ListCost"] = negative
    row["ContractedCost"] = negative
    adapter.on_credit_row(row, negative)
    return row


def split_allocation_row(rng: random.Random, i: int, remaining: int, profile, adapter) -> dict[str, str]:
    """A Split Cost Allocation row (FOCUS 1.3): a shared resource's cost allocated to a
    consuming workload. ``ResourceId`` is the shared resource; the ``Allocated*`` columns name
    the workload that received the split."""
    spec = profile.commitment_service  # shared compute host split across workloads
    region_id, region_name, azs = rng.choice(profile.regions)
    row, ctx = base_row(rng, profile, adapter)
    row["ChargePeriodStart"], row["ChargePeriodEnd"] = period(i, "hourly")
    _set_service(row, spec)
    shared_name = f"shared-host-{hexid(rng, 8)}"
    _set_resource_sku(rng, row, spec, ctx, region_id, region_name, shared_name, profile)
    row["AvailabilityZone"] = rng.choice(azs)

    quantity = q(Decimal(rng.uniform(0.05, 1.0)), QTY_Q)
    jitter = Decimal(rng.uniform(0.97, 1.03))
    list_unit = q(spec.unit_price_usd * jitter, PRICE_Q)
    contracted_unit = q(list_unit * PRIVATE_RATE, PRICE_Q)
    list_cost = q(list_unit * quantity, COST_Q)
    contracted_cost = q(contracted_unit * quantity, COST_Q)

    row["ChargeCategory"] = "Usage"
    row["ChargeFrequency"] = "Usage-Based"
    row["ChargeDescription"] = profile.split_allocation_description
    row["PricingCategory"] = "Standard"
    row["BilledCost"] = s(contracted_cost)
    row["EffectiveCost"] = s(contracted_cost)
    row["ListCost"] = s(list_cost)
    row["ContractedCost"] = s(contracted_cost)
    row["ListUnitPrice"] = s(list_unit)
    row["ContractedUnitPrice"] = s(contracted_unit)
    row["PricingQuantity"] = s(quantity)
    row["PricingUnit"] = spec.pricing_unit
    row["ConsumedQuantity"] = s(quantity)
    row["ConsumedUnit"] = spec.pricing_unit

    workload = rng.choice(ALLOCATION_WORKLOADS)
    method_id, method_details = rng.choice(ALLOCATION_METHODS)
    row["AllocatedMethodId"] = method_id
    # FOCUS 1.3 split allocation details: an Elements array exposing the allocated ratio and the
    # usage that drove the split (plus x_ method metadata). AllocatedRatio / UsageQuantity are
    # Numeric -> emitted as JSON numbers (single-source builder).
    element = {
        "AllocatedRatio": s(quantity),
        "UsageUnit": spec.pricing_unit,
        "UsageQuantity": s(quantity),
        **method_details,
    }
    row["AllocatedMethodDetails"] = allocated_method_details([element])
    row["AllocatedResourceId"] = profile.allocated_resource_id(rng, region_id, ctx, workload)
    row["AllocatedResourceName"] = f"workload-{workload}"
    row["AllocatedTags"] = json.dumps(
        {"workload": workload, profile.tag_keys[1]: rng.choice(COST_CENTERS)}, separators=(",", ":")
    )
    set_currency(row, "USD", list_unit, contracted_unit, contracted_cost)
    return row


def commitment_group(rng: random.Random, i0: int, remaining: int, profile, adapter) -> list[dict[str, str]]:
    """A commitment Purchase row + linked committed-usage rows (shared CommitmentDiscountId).

    The Purchase row carries the full commitment terms, which the Contract Commitment dataset
    re-derives so the two datasets join on ``ContractCommitmentId`` == ``CommitmentDiscountId``.
    """
    spec = profile.commitment_service
    commit = profile.commitment
    region_id, region_name, azs = rng.choice(profile.regions)
    az = rng.choice(azs)
    spend_based = rng.random() < 0.6

    commit_id = ""
    if commit.commit_id_before_base_row:
        commit_id = commit.commit_id(rng, region_id, "", spend_based)

    commit_name = commit.commit_name(spend_based)
    commit_type = commit.commit_type(spend_based)
    commit_category = commit.commit_category(spend_based)
    commit_unit = commit.commit_unit(spend_based)

    list_unit = q(spec.unit_price_usd, PRICE_Q)
    commit_unit_price = q(list_unit * COMMIT_RATE, PRICE_Q)
    upfront = q(commit_unit_price * COMMIT_TERM_HOURS, COST_Q)
    commit_total_qty = s(upfront) if spend_based else s(COMMIT_TERM_HOURS)

    purchase, ctx = base_row(rng, profile, adapter)
    if not commit.commit_id_before_base_row:
        commit_id = commit.commit_id(rng, region_id, ctx.sub_id, spend_based)

    purchase["ChargePeriodStart"] = iso(BILLING_START)
    purchase["ChargePeriodEnd"] = iso(BILLING_START + timedelta(hours=1))
    _set_service(purchase, spec)
    commit_resource = commit.commit_resource_name(rng, spend_based)
    purchase["ResourceId"] = commit_id
    purchase["ResourceName"] = commit_resource
    purchase["ResourceType"] = commit_type
    purchase["RegionId"] = region_id
    purchase["RegionName"] = region_name
    purchase["SkuId"] = commit.purchase_sku_id(rng)
    purchase["SkuMeter"] = "Commitment"
    purchase["SkuPriceId"] = profile.sku_price_id(rng)
    purchase["SkuPriceDetails"] = commit.purchase_sku_details(spend_based)
    purchase["ChargeCategory"] = "Purchase"
    purchase["ChargeFrequency"] = "One-Time"
    purchase["ChargeDescription"] = commit.purchase_description(commit_type)
    purchase["PricingCategory"] = "Standard"
    purchase["BilledCost"] = s(upfront)
    purchase["EffectiveCost"] = "0.000000"
    purchase["ListCost"] = s(upfront)
    purchase["ContractedCost"] = s(upfront)
    purchase["ListUnitPrice"] = s(upfront)
    purchase["ContractedUnitPrice"] = s(upfront)
    purchase["PricingQuantity"] = "1"
    purchase["PricingUnit"] = "Units"
    purchase["CommitmentDiscountId"] = commit_id
    purchase["CommitmentDiscountName"] = commit_name
    purchase["CommitmentDiscountCategory"] = commit_category
    purchase["CommitmentDiscountType"] = commit_type
    purchase["CommitmentDiscountQuantity"] = commit_total_qty
    purchase["CommitmentDiscountUnit"] = commit_unit
    set_currency(purchase, "USD", upfront, upfront, Decimal("0"))

    # Full billing identity of the commitment, reused verbatim by every linked usage row so
    # account/invoice grouping and reconciliation stay consistent within the group.
    billing_identity = {key: purchase[key] for key in adapter.commitment_identity_keys}
    contract_id = contract_id_for(commit_id)

    rows = [purchase]
    n_usage = min(remaining - 1, rng.randint(5, 9))
    for k in range(n_usage):
        usage, _ = base_row(rng, profile, adapter)
        usage.update(billing_identity)
        usage["ChargePeriodStart"], usage["ChargePeriodEnd"] = period(i0 + 1 + k, "hourly")
        _set_service(usage, spec)
        resource_name = profile.committed_resource_name(rng, spec, k)
        usage["RegionId"] = region_id
        usage["RegionName"] = region_name
        ref = ResourceRef(
            spec=spec, region_id=region_id, region_name=region_name,
            billing_id=ctx.billing_id, sub_id=ctx.sub_id, sub_name=ctx.sub_name,
            resource_name=resource_name,
        )
        usage["ResourceId"] = profile.resource_id(ref)
        usage["ResourceName"] = resource_name
        usage["ResourceType"] = spec.resource_type
        usage["AvailabilityZone"] = az
        usage["SkuId"] = profile.sku_id(rng, spec)
        usage["SkuMeter"] = spec.sku_meter
        usage["SkuPriceId"] = profile.sku_price_id(rng)
        usage["SkuPriceDetails"] = sku_price_details(dict(spec.sku_details))
        list_cost = q(list_unit, COST_Q)
        effective = q(commit_unit_price, COST_Q)
        usage["ChargeCategory"] = "Usage"
        usage["ChargeFrequency"] = "Usage-Based"
        usage["ChargeDescription"] = f"{spec.name} committed usage"
        usage["PricingCategory"] = "Committed"
        usage["BilledCost"] = "0.000000"  # covered by the upfront purchase
        usage["EffectiveCost"] = s(effective)  # amortised, < ListCost
        usage["ListCost"] = s(list_cost)
        usage["ContractedCost"] = s(effective)
        usage["ListUnitPrice"] = s(list_unit)
        usage["ContractedUnitPrice"] = s(commit_unit_price)
        usage["PricingQuantity"] = "1.0000"
        usage["PricingUnit"] = "Hours"
        usage["ConsumedQuantity"] = "1.0000"
        usage["ConsumedUnit"] = "Hours"
        usage["CommitmentDiscountId"] = commit_id
        usage["CommitmentDiscountName"] = commit_name
        usage["CommitmentDiscountCategory"] = commit_category
        usage["CommitmentDiscountType"] = commit_type
        usage["CommitmentDiscountStatus"] = "Used"
        usage["CommitmentDiscountQuantity"] = s(effective) if spend_based else "1.0000"
        usage["CommitmentDiscountUnit"] = commit_unit
        adapter.on_commit_usage(usage, commit_id, contract_id, s(effective))
        set_currency(usage, "USD", list_unit, commit_unit_price, effective)
        rows.append(usage)
    return rows
