"""FOCUS 1.3 adapter: 65-column Cost and Usage + 13-column Contract Commitment."""

from __future__ import annotations

from focus_data_toolkit.generators.engine.json_focus import contract_applied
from focus_data_toolkit.generators.providers.profile import ProviderProfile
from focus_data_toolkit.generators.versions.adapter import LadderBranch, VersionAdapter

COLUMNS: tuple[str, ...] = (
    "ProviderName",
    "PublisherName",
    "ServiceProviderName",
    "HostProviderName",
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
    "ContractApplied",
    "CapacityReservationId",
    "CapacityReservationStatus",
    "AllocatedMethodId",
    "AllocatedMethodDetails",
    "AllocatedResourceId",
    "AllocatedResourceName",
    "AllocatedTags",
    "Tags",
)

assert len(COLUMNS) == 65, f"FOCUS 1.3 Cost and Usage must have 65 columns, got {len(COLUMNS)}"
assert len(set(COLUMNS)) == 65, "FOCUS 1.3 Cost and Usage column names must be unique"

CONTRACT_COMMITMENT_COLUMNS: tuple[str, ...] = (
    "ContractCommitmentId",
    "ContractCommitmentType",
    "ContractCommitmentCategory",
    "ContractCommitmentCost",
    "ContractCommitmentQuantity",
    "ContractCommitmentUnit",
    "ContractCommitmentDescription",
    "ContractCommitmentPeriodStart",
    "ContractCommitmentPeriodEnd",
    "ContractId",
    "ContractPeriodStart",
    "ContractPeriodEnd",
    "BillingCurrency",
)

assert len(CONTRACT_COMMITMENT_COLUMNS) == 13, (
    f"FOCUS 1.3 Contract Commitment must have 13 columns, got {len(CONTRACT_COMMITMENT_COLUMNS)}"
)
assert len(set(CONTRACT_COMMITMENT_COLUMNS)) == 13, (
    "FOCUS 1.3 Contract Commitment column names must be unique"
)

# In 1.3 every linked usage row copies the full billing identity of the commitment.
_COMMITMENT_IDENTITY_KEYS: tuple[str, ...] = (
    "BillingAccountId",
    "BillingAccountName",
    "BillingAccountType",
    "SubAccountId",
    "SubAccountName",
    "SubAccountType",
    "InvoiceId",
)


def _fill_identity(row: dict, profile: ProviderProfile) -> None:
    row["ServiceProviderName"] = profile.service_provider_name
    row["HostProviderName"] = profile.host_provider_name
    # Multi-currency generator: PricingCurrency is never null (_set_currency overrides it for
    # priced rows; Tax/Credit keep this USD default).
    row["PricingCurrency"] = "USD"


def _on_tax(row: dict, amount_str: str) -> None:
    row["PricingCurrencyEffectiveCost"] = amount_str


def _on_credit(row: dict, negative_str: str) -> None:
    row["PricingCurrencyEffectiveCost"] = negative_str


def _on_commit_usage(usage: dict, commit_id: str, contract_id: str, effective_str: str) -> None:
    # FOCUS 1.3 ContractApplied: the JSON link to the Contract Commitment dataset.
    usage["ContractApplied"] = contract_applied(commit_id, contract_id, effective_str, "1.0000", "Hours")


V13 = VersionAdapter(
    version="1.3",
    default_seed=1302,
    columns=COLUMNS,
    contract_commitment_columns=CONTRACT_COMMITMENT_COLUMNS,
    ladder=(
        LadderBranch("credit", 0.05, requires_credits=True),
        LadderBranch("tax", 0.12),
        LadderBranch("purchase", 0.20),
        LadderBranch("split", 0.40),
        LadderBranch("commitment", 0.58, min_remaining=6, group=True),
    ),
    commitment_identity_keys=_COMMITMENT_IDENTITY_KEYS,
    emits_split_allocation=True,
    fill_version_identity=_fill_identity,
    on_tax_row=_on_tax,
    on_credit_row=_on_credit,
    on_commit_usage=_on_commit_usage,
)
