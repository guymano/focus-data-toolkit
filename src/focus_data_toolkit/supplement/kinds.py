"""Registry of supplement kinds — what a client may supply, and how it joins.

Each kind targets one FOCUS 1.4 dataset, joins to the conversion on that dataset's
natural key, and may supply a fixed set of fact columns (FOCUS column names). Anything
else must be ``x_``-prefixed. Join-key values are compared after ``.strip()`` (the same
normalization the converters use); there is no fuzzy matching.
"""

from __future__ import annotations

from dataclasses import dataclass

from focus_data_toolkit.convert.invoice_detail import GRAIN_FIELDS


@dataclass(frozen=True)
class SupplementKind:
    """One supplement file format the toolkit knows how to join and apply."""

    name: str
    target_dataset: str
    join_keys: tuple[str, ...]
    columns: frozenset[str]

    @property
    def header_template(self) -> tuple[str, ...]:
        """A ready-to-fill CSV header: join keys first, then the fact columns."""
        return self.join_keys + tuple(sorted(self.columns))


# The billing-period cycle facts a Cost and Usage source can never carry.
BILLING_PERIOD_KIND = SupplementKind(
    name="billing_period",
    target_dataset="Billing Period",
    join_keys=("InvoiceIssuerName", "BillingPeriodStart", "BillingPeriodEnd"),
    columns=frozenset(
        {"BillingPeriodCreated", "BillingPeriodLastUpdated", "BillingPeriodStatus"}
    ),
)

# Invoice-header facts: one row per issued invoice.
INVOICE_KIND = SupplementKind(
    name="invoice",
    target_dataset="Invoice Detail",
    join_keys=("InvoiceIssuerName", "InvoiceId"),
    columns=frozenset(
        {
            "InvoiceIssueDate",
            "InvoiceIssueStatus",
            "PaymentTerms",
            "PaymentDueDate",
            "ReferenceInvoiceId",
            "PurchaseOrderNumber",
            "PaymentCurrency",
        }
    ),
)

# Invoice-line facts: one row per invoice line, joined on the full business grain
# (exactly the grain the derived Invoice Detail dataset aggregates on). ``BilledCost``
# is accepted only as a reconciliation check against the derived grain sum — it never
# replaces the derived value.
INVOICE_LINE_KIND = SupplementKind(
    name="invoice_line",
    target_dataset="Invoice Detail",
    join_keys=GRAIN_FIELDS,
    columns=frozenset(
        {
            "InvoiceDetailId",
            "InvoiceDetailCreated",
            "InvoiceDetailLastUpdated",
            "InvoiceDetailDescription",
            "InvoiceDetailGrain",
            "PaymentCurrencyBilledCost",
            "PaymentCurrencyInvoiceDetailId",
            "BilledCost",
        }
    ),
)

# The 1.4-new contract-commitment commercial terms a 1.3 source does not carry.
CONTRACT_COMMITMENT_KIND = SupplementKind(
    name="contract_commitment",
    target_dataset="Contract Commitment",
    join_keys=("ContractCommitmentId",),
    columns=frozenset(
        {
            "ContractCommitmentApplicability",
            "ContractCommitmentBenefitCategory",
            "ContractCommitmentCreated",
            "ContractCommitmentDiscountPercentage",
            "ContractCommitmentFulfillmentInterval",
            "ContractCommitmentLastUpdated",
            "ContractCommitmentLifecycleStatus",
            "ContractCommitmentModel",
            "ContractCommitmentOfferCategory",
            "ContractCommitmentPaymentInterval",
            "ContractCommitmentPaymentModel",
            "ContractCommitmentPaymentUpfrontPercentage",
            "InvoiceIssuerName",
            "ServiceProviderName",
        }
    ),
)

SUPPLEMENT_KINDS: dict[str, SupplementKind] = {
    k.name: k
    for k in (BILLING_PERIOD_KIND, INVOICE_KIND, INVOICE_LINE_KIND, CONTRACT_COMMITMENT_KIND)
}


def kinds_for_column(dataset: str, column: str) -> tuple[SupplementKind, ...]:
    """The kinds able to supply ``column`` of ``dataset`` (deterministic order)."""
    return tuple(
        kind
        for kind in SUPPLEMENT_KINDS.values()
        if kind.target_dataset == dataset and column in kind.columns
    )
