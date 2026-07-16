"""Expand a FOCUS 1.3 Contract Commitment dataset (13 columns) to 1.4 (30 columns).

The 17 columns FOCUS 1.4 adds are populated as follows:

* Derived from the source or the Cost and Usage context:
  ``ContractCommitmentCreated`` / ``ContractCommitmentLastUpdated`` (period
  start), ``ContractCommitmentDurationType`` (from the commitment period),
  ``InvoiceIssuerName`` / ``ServiceProviderName`` (provider context),
  ``PricingCurrency`` (billing currency),
  ``PricingCurrencyContractCommitmentCost`` (commitment cost).
* Deterministic documented defaults (the 1.3 source carries no equivalent):
  ``ContractCommitmentBenefitCategory="Discount"``,
  ``ContractCommitmentFulfillmentInterval="Monthly"``,
  ``ContractCommitmentLifecycleStatus="Active"``,
  ``ContractCommitmentModel="Continuous"``,
  ``ContractCommitmentOfferCategory="Public"``,
  ``ContractCommitmentPaymentInterval="Monthly"``,
  ``ContractCommitmentPaymentModel="No Upfront"`` (with
  ``ContractCommitmentPaymentUpfrontPercentage="0"`` for cross-field
  consistency), and an explanatory ``ContractCommitmentApplicability`` JSON
  object.
* Null where the model allows it: ``ContractCommitmentDiscountPercentage``.
"""

from __future__ import annotations

import json
from datetime import datetime

from focus_data_toolkit.model import dataset_columns
from focus_data_toolkit.provenance import ColumnRule, Lineage

DATASET = "Contract Commitment"

# Provenance of every 1.4 Contract Commitment column. The 13 source columns are
# OBSERVED; a few are derived/enriched; the 1.4-new commercial terms are ASSUMED
# (no source), which blocks strict production.
_OBSERVED_FROM_1_3 = (
    "BillingCurrency", "ContractCommitmentCategory", "ContractCommitmentCost",
    "ContractCommitmentDescription", "ContractCommitmentId", "ContractCommitmentPeriodEnd",
    "ContractCommitmentPeriodStart", "ContractCommitmentQuantity", "ContractCommitmentType",
    "ContractCommitmentUnit", "ContractId", "ContractPeriodEnd", "ContractPeriodStart",
)
PROVENANCE: dict[str, ColumnRule] = {
    **{c: ColumnRule(Lineage.OBSERVED, f"ContractCommitment.{c}") for c in _OBSERVED_FROM_1_3},
    "ContractCommitmentDurationType": ColumnRule(Lineage.DERIVED, "commitment period span"),
    "InvoiceIssuerName": ColumnRule(Lineage.ENRICHED, "Cost and Usage provider context"),
    "ServiceProviderName": ColumnRule(Lineage.ENRICHED, "Cost and Usage provider context"),
    "PricingCurrency": ColumnRule(Lineage.DERIVED, "ContractCommitment.BillingCurrency"),
    "PricingCurrencyContractCommitmentCost": ColumnRule(
        Lineage.DERIVED, "ContractCommitment.ContractCommitmentCost"
    ),
    "ContractCommitmentCreated": ColumnRule(Lineage.ASSUMED, note="provider record timestamp"),
    "ContractCommitmentLastUpdated": ColumnRule(Lineage.ASSUMED, note="provider record timestamp"),
    "ContractCommitmentDiscountPercentage": ColumnRule(Lineage.UNAVAILABLE, note="emitted null"),
    "ContractCommitmentApplicability": ColumnRule(Lineage.ASSUMED, note="terms absent from source"),
    "ContractCommitmentBenefitCategory": ColumnRule(Lineage.ASSUMED, note="assumed default"),
    "ContractCommitmentFulfillmentInterval": ColumnRule(Lineage.ASSUMED, note="assumed default"),
    "ContractCommitmentLifecycleStatus": ColumnRule(Lineage.ASSUMED, note="assumed default"),
    "ContractCommitmentModel": ColumnRule(Lineage.ASSUMED, note="assumed default"),
    "ContractCommitmentOfferCategory": ColumnRule(Lineage.ASSUMED, note="assumed default"),
    "ContractCommitmentPaymentInterval": ColumnRule(Lineage.ASSUMED, note="assumed default"),
    "ContractCommitmentPaymentModel": ColumnRule(Lineage.ASSUMED, note="assumed default"),
    "ContractCommitmentPaymentUpfrontPercentage": ColumnRule(Lineage.ASSUMED, note="assumed default"),
}

_APPLICABILITY = json.dumps(
    {"x_Source": "Derived from a FOCUS 1.3 Contract Commitment dataset; "
                 "applicability terms were not present in the source."},
    separators=(",", ":"),
)

_DEFAULTS = {
    "ContractCommitmentApplicability": _APPLICABILITY,
    "ContractCommitmentBenefitCategory": "Discount",
    "ContractCommitmentDiscountPercentage": "",
    "ContractCommitmentFulfillmentInterval": "Monthly",
    "ContractCommitmentLifecycleStatus": "Active",
    "ContractCommitmentModel": "Continuous",
    "ContractCommitmentOfferCategory": "Public",
    "ContractCommitmentPaymentInterval": "Monthly",
    "ContractCommitmentPaymentModel": "No Upfront",
    "ContractCommitmentPaymentUpfrontPercentage": "0",
}


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_type(start: str, end: str) -> str:
    """Return an Expected-Format duration like ``"12 Months"`` from the period."""
    a, b = _parse(start or ""), _parse(end or "")
    if a is None or b is None or b <= a:
        return "12 Months"
    months = max(1, round((b - a).days / 30.44))
    return f"{months} Months" if months > 1 else "1 Month"


def convert_contract_commitment(
    rows: list[dict[str, str]],
    *,
    service_provider_name: str,
    invoice_issuer_name: str,
) -> list[dict[str, str]]:
    """Return the 13-column 1.3 ``rows`` expanded to the 1.4 30-column shape."""
    target = dataset_columns(DATASET)
    out: list[dict[str, str]] = []
    for row in rows:
        created = row.get("ContractCommitmentPeriodStart", "")
        converted: dict[str, str] = {}
        for col in target:
            if col in row:
                converted[col] = row[col]
            elif col == "ContractCommitmentCreated":
                converted[col] = created
            elif col == "ContractCommitmentLastUpdated":
                converted[col] = created
            elif col == "ContractCommitmentDurationType":
                converted[col] = _duration_type(
                    row.get("ContractCommitmentPeriodStart", ""),
                    row.get("ContractCommitmentPeriodEnd", ""),
                )
            elif col == "InvoiceIssuerName":
                converted[col] = invoice_issuer_name
            elif col == "ServiceProviderName":
                converted[col] = service_provider_name
            elif col == "PricingCurrency":
                converted[col] = row.get("BillingCurrency", "")
            elif col == "PricingCurrencyContractCommitmentCost":
                converted[col] = row.get("ContractCommitmentCost", "")
            elif col in _DEFAULTS:
                converted[col] = _DEFAULTS[col]
            else:
                converted[col] = ""
        out.append(converted)
    return out
