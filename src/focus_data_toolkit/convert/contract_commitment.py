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

from focus_data_toolkit.errors import Diagnostic, Severity
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

# The official ContractCommitmentApplicability object schema requires a scope
# representation: when neither IsGlobalScope nor IsComplexScope is true, Inclusions
# (min 1) and InclusionOperator become required. The authoritative terms are unknown
# here, so the minimal conformant synthetic object declares a complex scope; the
# value stays ASSUMED and never passes strict mode.
_APPLICABILITY = json.dumps(
    {"IsComplexScope": True,
     "x_Source": "Synthetic applicability derived from a FOCUS 1.3 Contract Commitment "
                 "dataset; authoritative applicability terms were not present in the source."},
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
    """Return an Expected-Format duration like ``"12 Months"`` from the period.

    An unparseable or inverted period yields ``""`` (never a fabricated duration):
    the value cannot be derived from the source, and the mandatory-column lint will
    flag the row rather than silently publish an arbitrary ``"12 Months"``.
    """
    a, b = _parse(start or ""), _parse(end or "")
    if a is None or b is None or b <= a:
        return ""
    months = max(1, round((b - a).days / 30.44))
    return f"{months} Months" if months > 1 else "1 Month"


# How many offending ContractCommitmentIds a diagnostic lists inline.
_ID_SAMPLE_CAP = 25


def convert_contract_commitment(
    rows: list[dict[str, str]],
    *,
    service_provider_name: str,
    invoice_issuer_name: str,
    diagnostics: list[Diagnostic] | None = None,
) -> list[dict[str, str]]:
    """Return the 13-column 1.3 ``rows`` expanded to the 1.4 30-column shape.

    Rows whose commitment period cannot be parsed get an empty
    ``ContractCommitmentDurationType`` (the duration is not derivable) and are
    reported through ``diagnostics`` as a single aggregated ``FDT-CC-001`` WARNING.
    """
    target = dataset_columns(DATASET)
    out: list[dict[str, str]] = []
    unparseable_ids: list[str] = []
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
                duration = _duration_type(
                    row.get("ContractCommitmentPeriodStart", ""),
                    row.get("ContractCommitmentPeriodEnd", ""),
                )
                if not duration:
                    unparseable_ids.append(row.get("ContractCommitmentId", ""))
                converted[col] = duration
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
    if unparseable_ids and diagnostics is not None:
        diagnostics.append(
            Diagnostic(
                code="FDT-CC-001",
                severity=Severity.WARNING,
                message="commitment period unparseable or inverted; "
                "ContractCommitmentDurationType left empty (not derivable)",
                datasets=(DATASET,),
                context={
                    "row_count": str(len(unparseable_ids)),
                    "contract_commitment_ids": ", ".join(
                        sorted(set(unparseable_ids))[:_ID_SAMPLE_CAP]
                    ),
                },
            )
        )
    return out
