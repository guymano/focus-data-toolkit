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
from decimal import Decimal

from focus_data_toolkit.generators.engine.allocation_math import residue_ratios, residue_shares
from focus_data_toolkit.generators.engine.context import ResourceRef, RowContext
from focus_data_toolkit.generators.engine.determinism import (
    BILLING_END,
    BILLING_START,
    COMMIT_RATE,
    COST_CENTERS,
    COST_Q,
    ENVIRONMENTS,
    OWNERS,
    PRICE_Q,
    PRIVATE_RATE,
    QTY_Q,
    contract_id_for,
    exact_cost,
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
    # Multi-currency generator: PricingCurrency is never null in either version
    # (set_currency overrides it for priced rows; Tax/Credit keep this USD default).
    row["PricingCurrency"] = "USD"
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
    list_cost = exact_cost(list_unit, quantity)
    contracted_cost = exact_cost(contracted_unit, quantity)

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
    # The negotiated contract terms (rate card / minimum spend / usage commitment) are
    # what the PRIVATE_RATE contracted price *is*: every on-demand usage row is priced
    # under the negotiated rate card and its spend counts toward the contracted
    # minimum, while only usage of the commitment-eligible service — measured in the
    # usage commitment's own unit — counts toward that commitment. The 1.3 adapter
    # links the row to those terms via ContractApplied; 1.2 has no such column.
    adapter.on_negotiated_usage(row, profile, spec)
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
    # Tax is priced in the billing currency (USD default from base_row), so the
    # pricing-currency effective cost mirrors EffectiveCost in both versions.
    row["PricingCurrencyEffectiveCost"] = amount_str
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
    # Same-currency mirror as on Tax rows (USD default from base_row).
    row["PricingCurrencyEffectiveCost"] = negative
    return row


def split_allocation_group_rows(
    rng: random.Random, i0: int, remaining: int, profile, adapter
) -> list[dict[str, str]]:
    """A coherent Split Cost Allocation group (FOCUS 1.3): one shared host charge fully
    allocated to 2-3 distinct workloads in a single charge period.

    ``ResourceId`` is the shared resource on every row; the ``Allocated*`` columns name
    the workload that received each split. ``AllocatedRatio`` values sum to exactly 1
    and every cost column (List / Contracted / Billed / Effective) sums to exactly the
    host amount: the quantity shares absorb the residue (last consumer), and each row's
    costs are exact unit-price x quantity products, so per-row cost arithmetic and
    per-group conservation hold at once (distributivity).
    """
    spec = profile.commitment_service  # shared compute host split across workloads
    region_id, region_name, azs = rng.choice(profile.regions)
    host, ctx = base_row(rng, profile, adapter)
    host["ChargePeriodStart"], host["ChargePeriodEnd"] = period(i0, "hourly")
    _set_service(host, spec)
    shared_name = f"shared-host-{hexid(rng, 8)}"
    _set_resource_sku(rng, host, spec, ctx, region_id, region_name, shared_name, profile)
    host["AvailabilityZone"] = rng.choice(azs)

    quantity = q(Decimal(rng.uniform(2.0, 8.0)), QTY_Q)
    jitter = Decimal(rng.uniform(0.97, 1.03))
    list_unit = q(spec.unit_price_usd * jitter, PRICE_Q)
    contracted_unit = q(list_unit * PRIVATE_RATE, PRICE_Q)

    host["ChargeCategory"] = "Usage"
    host["ChargeFrequency"] = "Usage-Based"
    host["ChargeDescription"] = profile.split_allocation_description
    host["PricingCategory"] = "Standard"
    host["ListUnitPrice"] = s(list_unit)
    host["ContractedUnitPrice"] = s(contracted_unit)
    host["PricingUnit"] = spec.pricing_unit
    host["ConsumedUnit"] = spec.pricing_unit

    n = 3 if remaining >= 3 else 2
    workloads = rng.sample(ALLOCATION_WORKLOADS, n)
    method_id, method_details = rng.choice(ALLOCATION_METHODS)
    weights = [Decimal(rng.randint(1, 5)) for _ in range(n)]
    qty_shares = residue_shares(quantity, residue_ratios(weights), QTY_Q)
    # Display ratios derive from the actual quantity shares, so ratio and usage agree;
    # the last ratio absorbs the residue and the group sums to exactly 1.
    ratios = residue_ratios(qty_shares)

    rows: list[dict[str, str]] = []
    for k, workload in enumerate(workloads):
        row = dict(host)
        share_qty = qty_shares[k]
        contracted_cost = exact_cost(contracted_unit, share_qty)
        row["BilledCost"] = s(contracted_cost)
        row["EffectiveCost"] = s(contracted_cost)
        row["ListCost"] = s(exact_cost(list_unit, share_qty))
        row["ContractedCost"] = s(contracted_cost)
        row["PricingQuantity"] = s(share_qty)
        row["ConsumedQuantity"] = s(share_qty)
        row["AllocatedMethodId"] = method_id
        # FOCUS 1.3 split allocation details: an Elements array exposing the allocated
        # ratio and the usage that drove the split (plus x_ method metadata).
        # AllocatedRatio / UsageQuantity are Numeric -> emitted as JSON numbers.
        element = {
            "AllocatedRatio": s(ratios[k]),
            "UsageUnit": spec.pricing_unit,
            "UsageQuantity": s(share_qty),
            **method_details,
        }
        row["AllocatedMethodDetails"] = allocated_method_details([element])
        row["AllocatedResourceId"] = profile.allocated_resource_id(rng, region_id, ctx, workload)
        row["AllocatedResourceName"] = f"workload-{workload}"
        row["AllocatedTags"] = json.dumps(
            {"workload": workload, profile.tag_keys[1]: rng.choice(COST_CENTERS)},
            separators=(",", ":"),
        )
        set_currency(row, "USD", list_unit, contracted_unit, contracted_cost)
        # The shared host is ordinary on-demand usage of the commitment-eligible
        # compute service: the negotiated terms apply to it like to any other
        # Standard usage row (and its Hours usage counts toward the usage
        # commitment), which also keeps those terms structurally reachable.
        adapter.on_negotiated_usage(row, profile, spec)
        rows.append(row)
    return rows


def commitment_group(rng: random.Random, i0: int, remaining: int, profile, adapter) -> list[dict[str, str]]:
    """Recurring per-charge-period commitment blocks that reconcile exactly.

    FOCUS amortises a commitment discount evenly over each charge period of its term
    (use-it-or-lose-it), so each hourly period carries one Recurring Purchase row
    (``BilledCost`` = the per-period fee, ``EffectiveCost`` = 0), Used rows for the
    consumed capacity and one Unused row absorbing the remainder. Every amount is an
    exact product of the same commitment unit price with quantities summing to the
    committed capacity, so ``sum(Usage.EffectiveCost) == sum(Purchase.BilledCost)``
    holds under exact Decimal equality per charge period — and therefore per billing
    period. Only whole periods are emitted (a truncated period would break the
    invariant). The Purchase rows carry the full commitment terms, which the Contract
    Commitment dataset re-derives so the two datasets join on
    ``ContractCommitmentId`` == ``CommitmentDiscountId``.
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
    # Three prices kept apart: the negotiated (contracted) rate excludes the commitment
    # discount, which shows only between ContractedCost and EffectiveCost — so
    # EffectiveCost < ContractedCost <= ListCost on every Used row.
    contracted_unit = q(list_unit * PRIVATE_RATE, PRICE_Q)
    capacity = Decimal(rng.randint(2, 4))  # committed hours per charge period
    fee = exact_cost(commit_unit_price, capacity)  # the recurring per-period Purchase cost

    template, ctx = base_row(rng, profile, adapter)
    if not commit.commit_id_before_base_row:
        commit_id = commit.commit_id(rng, region_id, ctx.sub_id, spend_based)

    _set_service(template, spec)
    commit_resource = commit.commit_resource_name(rng, spend_based)
    template["ResourceId"] = commit_id
    template["ResourceName"] = commit_resource
    template["ResourceType"] = commit_type
    template["RegionId"] = region_id
    template["RegionName"] = region_name
    template["SkuId"] = commit.purchase_sku_id(rng)
    template["SkuMeter"] = "Commitment"
    template["SkuPriceId"] = profile.sku_price_id(rng)
    template["SkuPriceDetails"] = commit.purchase_sku_details(spend_based)
    template["CommitmentDiscountId"] = commit_id
    template["CommitmentDiscountName"] = commit_name
    template["CommitmentDiscountCategory"] = commit_category
    template["CommitmentDiscountType"] = commit_type
    template["CommitmentDiscountUnit"] = commit_unit

    # Full billing identity of the commitment, reused verbatim by every row of the group so
    # account/invoice grouping and reconciliation stay consistent within the group.
    billing_identity = {key: template[key] for key in adapter.commitment_identity_keys}
    contract_id = contract_id_for(commit_id)

    rows: list[dict[str, str]] = []
    n_periods = 2 if remaining >= 8 else 1  # whole 4-row blocks only, never truncated
    used_index = 0
    for p in range(n_periods):
        start, end = period(i0 + p, "hourly")

        purchase = dict(template)
        purchase["ChargePeriodStart"] = start
        purchase["ChargePeriodEnd"] = end
        purchase["ChargeCategory"] = "Purchase"
        purchase["ChargeFrequency"] = "Recurring"
        purchase["ChargeDescription"] = commit.purchase_description(commit_type)
        purchase["PricingCategory"] = "Standard"
        purchase["BilledCost"] = s(fee)
        purchase["EffectiveCost"] = "0"  # amortised into the covered usage rows
        purchase["ListCost"] = s(fee)
        purchase["ContractedCost"] = s(fee)
        if spend_based:
            # A spend commitment prices a monetary block: the quantity IS the committed
            # spend in the pricing currency at a unit price of 1.00 (FOCUS spend-based
            # Purchase modelling), and the discount quantity is the same amount.
            purchase["ListUnitPrice"] = "1"
            purchase["ContractedUnitPrice"] = "1"
            purchase["PricingQuantity"] = s(fee)
            purchase["PricingUnit"] = "USD"
            purchase["CommitmentDiscountQuantity"] = s(fee)
            adapter.on_commit_usage(purchase, commit_id, contract_id, s(fee), "", "")
            set_currency(purchase, "USD", Decimal("1"), Decimal("1"), Decimal("0"))
        else:
            # A usage commitment purchases the committed capacity at the commitment
            # rate; the discount quantity is that capacity in its native unit. The
            # ContractApplied element carries the quantity branch (usage semantics).
            purchase["ListUnitPrice"] = s(commit_unit_price)
            purchase["ContractedUnitPrice"] = s(commit_unit_price)
            purchase["PricingQuantity"] = s(capacity)
            purchase["PricingUnit"] = "Hours"
            purchase["CommitmentDiscountQuantity"] = s(capacity)
            adapter.on_commit_usage(purchase, commit_id, contract_id, "", s(capacity), "Hours")
            set_currency(purchase, "USD", commit_unit_price, commit_unit_price, Decimal("0"))
        rows.append(purchase)

        consumed = Decimal("0")
        for _k in range(2):
            used_qty = q(Decimal(rng.uniform(0.25, float(capacity) / 2 - 0.25)), QTY_Q)
            consumed += used_qty
            usage, _ = base_row(rng, profile, adapter)
            usage.update(billing_identity)
            usage["ChargePeriodStart"] = start
            usage["ChargePeriodEnd"] = end
            _set_service(usage, spec)
            resource_name = profile.committed_resource_name(rng, spec, used_index)
            used_index += 1
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
            effective = exact_cost(commit_unit_price, used_qty)
            usage["ChargeCategory"] = "Usage"
            usage["ChargeFrequency"] = "Usage-Based"
            usage["ChargeDescription"] = f"{spec.name} committed usage"
            usage["PricingCategory"] = "Committed"
            usage["BilledCost"] = "0"  # covered by the recurring purchase
            usage["EffectiveCost"] = s(effective)  # amortised commitment rate
            usage["ListCost"] = s(exact_cost(list_unit, used_qty))
            usage["ContractedCost"] = s(exact_cost(contracted_unit, used_qty))
            usage["ListUnitPrice"] = s(list_unit)
            usage["ContractedUnitPrice"] = s(contracted_unit)
            usage["PricingQuantity"] = s(used_qty)
            usage["PricingUnit"] = "Hours"
            usage["ConsumedQuantity"] = s(used_qty)
            usage["ConsumedUnit"] = "Hours"
            usage["CommitmentDiscountId"] = commit_id
            usage["CommitmentDiscountName"] = commit_name
            usage["CommitmentDiscountCategory"] = commit_category
            usage["CommitmentDiscountType"] = commit_type
            usage["CommitmentDiscountStatus"] = "Used"
            usage["CommitmentDiscountQuantity"] = s(effective) if spend_based else s(used_qty)
            usage["CommitmentDiscountUnit"] = commit_unit
            if spend_based:
                adapter.on_commit_usage(usage, commit_id, contract_id, s(effective), "", "")
            else:
                # Usage commitments apply the measured quantity, not a cost (the
                # quantity branch survives the 1.4 oneOf conversion unchanged).
                adapter.on_commit_usage(usage, commit_id, contract_id, "", s(used_qty), "Hours")
            set_currency(usage, "USD", list_unit, contracted_unit, effective)
            rows.append(usage)

        # Use-it-or-lose-it: the wasted remainder is an Unused usage row charged to the
        # commitment itself, closing the per-period reconciliation exactly.
        waste = capacity - consumed
        wasted_effective = exact_cost(commit_unit_price, waste)
        unused = dict(template)
        unused["ChargePeriodStart"] = start
        unused["ChargePeriodEnd"] = end
        unused["ChargeCategory"] = "Usage"
        unused["ChargeFrequency"] = "Usage-Based"
        unused["ChargeDescription"] = f"{commit_name} unused commitment"
        unused["PricingCategory"] = "Committed"
        unused["BilledCost"] = "0"
        unused["EffectiveCost"] = s(wasted_effective)
        if spend_based:
            # The unused part of a spend commitment is a monetary block too: the
            # quantity is the unused committed spend in USD at a unit price of 1.00.
            unused["ListCost"] = s(wasted_effective)
            unused["ContractedCost"] = s(wasted_effective)
            unused["ListUnitPrice"] = "1"
            unused["ContractedUnitPrice"] = "1"
            unused["PricingQuantity"] = s(wasted_effective)
            unused["PricingUnit"] = "USD"
            unused["CommitmentDiscountQuantity"] = s(wasted_effective)
            adapter.on_commit_usage(unused, commit_id, contract_id, s(wasted_effective), "", "")
            set_currency(unused, "USD", Decimal("1"), Decimal("1"), wasted_effective)
        else:
            unused["ListCost"] = s(exact_cost(list_unit, waste))
            unused["ContractedCost"] = s(exact_cost(contracted_unit, waste))
            unused["ListUnitPrice"] = s(list_unit)
            unused["ContractedUnitPrice"] = s(contracted_unit)
            unused["PricingQuantity"] = s(waste)
            unused["PricingUnit"] = "Hours"
            unused["CommitmentDiscountQuantity"] = s(waste)
            adapter.on_commit_usage(unused, commit_id, contract_id, "", s(waste), "Hours")
            set_currency(unused, "USD", list_unit, contracted_unit, wasted_effective)
        unused["CommitmentDiscountStatus"] = "Unused"
        rows.append(unused)
    return rows
