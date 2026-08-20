"""FOCUS 1.3 adapter: 65-column Cost and Usage + 13-column Contract Commitment."""

from __future__ import annotations

from focus_data_toolkit.generators.engine.determinism import (
    negotiated_commitment_id,
    negotiated_contract_id,
)
from focus_data_toolkit.generators.engine.json_focus import (
    contract_applied,
    contract_applied_element,
)
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


def _on_commit_usage(
    usage: dict,
    commit_id: str,
    contract_id: str,
    applied_cost: str,
    applied_qty: str,
    applied_unit: str,
) -> None:
    # FOCUS 1.3 ContractApplied: the JSON link to the Contract Commitment dataset, on
    # every commitment row — the recurring Purchase (whose ResourceId is the
    # commitment, per rule CAU-ContractAppliedObject-O-039-C) and the Used and Unused
    # usage rows alike (an Unused row is still a charge tied to the commitment).
    # Spend commitments apply a cost alone; usage commitments also carry the measured
    # quantity in its native unit.
    usage["ContractApplied"] = contract_applied(
        [contract_applied_element(commit_id, contract_id, applied_cost, applied_qty, applied_unit)]
    )


def _on_negotiated_usage(row: dict, profile: ProviderProfile) -> None:
    # Eligible on-demand usage is linked to the three negotiated (non-discount) contract
    # terms: the negotiated rate card explains the ContractedCost, the spend counts
    # toward the contracted minimum, and the consumed quantity counts toward the usage
    # commitment. These commitments are reachable ONLY through ContractApplied (they
    # are not CommitmentDiscountIds) — the FOCUS-defined dataset relationship.
    contract = negotiated_contract_id(profile.key)
    row["ContractApplied"] = contract_applied(
        [
            contract_applied_element(
                negotiated_commitment_id("RATECARD", profile.key), contract, row["ContractedCost"]
            ),
            contract_applied_element(
                negotiated_commitment_id("MINSPEND", profile.key), contract, row["ContractedCost"]
            ),
            contract_applied_element(
                negotiated_commitment_id("USAGEMIN", profile.key),
                contract,
                row["ContractedCost"],
                row["ConsumedQuantity"],
                row["ConsumedUnit"],
            ),
        ]
    )


V13 = VersionAdapter(
    version="1.3",
    default_seed=1302,
    columns=COLUMNS,
    contract_commitment_columns=CONTRACT_COMMITMENT_COLUMNS,
    ladder=(
        LadderBranch("credit", 0.05, requires_credits=True),
        LadderBranch("tax", 0.12),
        LadderBranch("purchase", 0.20),
        LadderBranch("split", 0.40, min_remaining=2, group=True),
        LadderBranch("commitment", 0.58, min_remaining=6, group=True),
    ),
    commitment_identity_keys=_COMMITMENT_IDENTITY_KEYS,
    emits_split_allocation=True,
    fill_version_identity=_fill_identity,
    on_commit_usage=_on_commit_usage,
    on_negotiated_usage=_on_negotiated_usage,
)
