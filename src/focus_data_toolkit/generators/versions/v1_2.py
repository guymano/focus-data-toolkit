"""FOCUS 1.2 adapter: 57-column Cost and Usage, no Contract Commitment dataset."""

from __future__ import annotations

from focus_data_toolkit.generators.versions.adapter import LadderBranch, VersionAdapter

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

# Every linked usage row copies the full billing identity of the commitment purchase, so
# each BillingAccountId keeps a single (BillingAccountName, InvoiceId) across the group
# (historically 1.2 copied only the account ids, leaving name/invoice to diverge).
_COMMITMENT_IDENTITY_KEYS: tuple[str, ...] = (
    "BillingAccountId",
    "BillingAccountName",
    "BillingAccountType",
    "SubAccountId",
    "SubAccountName",
    "SubAccountType",
    "InvoiceId",
)


def _noop_identity(row: dict, profile: object) -> None:
    """1.2 has no ServiceProviderName/HostProviderName columns to fill."""


def _noop_commit_usage(
    usage: dict,
    commit_id: str,
    contract_id: str,
    applied_cost: str,
    applied_qty: str,
    applied_unit: str,
) -> None:
    """1.2 has no ContractApplied column."""


def _noop_negotiated_usage(row: dict, profile: object) -> None:
    """1.2 has no ContractApplied column, so negotiated terms are not linkable."""


V12 = VersionAdapter(
    version="1.2",
    default_seed=1202,
    columns=COLUMNS,
    contract_commitment_columns=None,
    ladder=(
        LadderBranch("credit", 0.05, requires_credits=True),
        LadderBranch("tax", 0.12),
        LadderBranch("purchase", 0.20),
        LadderBranch("commitment", 0.45, min_remaining=6, group=True),
    ),
    commitment_identity_keys=_COMMITMENT_IDENTITY_KEYS,
    emits_split_allocation=False,
    fill_version_identity=_noop_identity,
    on_commit_usage=_noop_commit_usage,
    on_negotiated_usage=_noop_negotiated_usage,
)
