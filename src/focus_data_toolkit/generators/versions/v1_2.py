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

# In 1.2 the commitment usage rows copied only the account ids from the purchase.
_COMMITMENT_IDENTITY_KEYS: tuple[str, ...] = (
    "BillingAccountId",
    "SubAccountId",
    "SubAccountName",
)


def _noop_identity(row: dict, profile: object) -> None:
    """1.2 has no ServiceProviderName/HostProviderName and does not seed PricingCurrency."""


def _noop_tax(row: dict, amount_str: str) -> None:
    """1.2 leaves PricingCurrencyEffectiveCost null on Tax."""


def _noop_credit(row: dict, negative_str: str) -> None:
    """1.2 leaves PricingCurrencyEffectiveCost null on Credit."""


def _noop_commit_usage(usage: dict, commit_id: str, contract_id: str, effective_str: str) -> None:
    """1.2 has no ContractApplied column."""


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
    on_tax_row=_noop_tax,
    on_credit_row=_noop_credit,
    on_commit_usage=_noop_commit_usage,
)
