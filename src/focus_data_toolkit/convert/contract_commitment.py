"""Expand a FOCUS 1.3 Contract Commitment dataset (13 columns) to 1.4 (30 columns).

The 17 columns FOCUS 1.4 adds are populated as follows:

* Derived from the source or the Cost and Usage context:
  ``ContractCommitmentCreated`` / ``ContractCommitmentLastUpdated`` (period
  start), ``ContractCommitmentDurationType`` (from the commitment period, only when
  it spans a whole number of calendar months; otherwise it must be supplied, and only
  synthetic mode assumes the nearest whole-month value),
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

import calendar
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from focus_data_toolkit.errors import Diagnostic, Severity
from focus_data_toolkit.model import dataset_columns
from focus_data_toolkit.provenance import ColumnRule, Lineage, LineageCounters

if TYPE_CHECKING:
    from focus_data_toolkit.supplement.loader import SupplementTable

DATASET = "Contract Commitment"
DURATION = "ContractCommitmentDurationType"

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
    # Settled per row after supplements (see settle_duration_type); this is the rule when
    # every period spans whole calendar months.
    DURATION: ColumnRule(
        Lineage.DERIVED,
        "commitment period span",
        note="whole calendar months only; any other span must be supplied",
    ),
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
    """Parse a FOCUS timestamp; a naive value is taken as UTC (FOCUS requires UTC)."""
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _add_months(ts: datetime, months: int) -> datetime:
    """``ts`` shifted by ``months`` calendar months, the day clamped to the month's last day."""
    total = ts.month - 1 + months
    year, month = ts.year + total // 12, total % 12 + 1
    return ts.replace(year=year, month=month, day=min(ts.day, calendar.monthrange(year, month)[1]))


def format_duration(months: int) -> str:
    """FOCUS 1.4 duration for a whole number of months: years when whole, as in the spec."""
    if months % 12 == 0:
        years = months // 12
        return "1 Year" if years == 1 else f"{years} Years"
    return "1 Month" if months == 1 else f"{months} Months"


def _duration_type(start: str, end: str) -> str:
    """Return an Expected-Format duration such as ``"1 Year"`` from the period.

    The duration is derived only when the (exclusive) end is exactly the start shifted
    by a whole number of calendar months (Jan 31 + 1 month = Feb 28/29). Any other span
    is not a standard offering length that the dates prove (FOCUS says the duration
    "MAY differ" from the actual period), so it yields ``""``, as do unparseable or
    inverted periods.
    """
    a, b = _parse(start or ""), _parse(end or "")
    if a is None or b is None or b <= a:
        return ""
    b = b.astimezone(a.tzinfo)  # count calendar months in the start's frame
    months = (b.year - a.year) * 12 + (b.month - a.month)
    if months < 1 or _add_months(a, months) != b:
        return ""
    return format_duration(months)


def _rounded_duration(start: str, end: str) -> str:
    """The nearest whole-month duration: a synthetic-mode assumption, never a strict fact."""
    a, b = _parse(start or ""), _parse(end or "")
    if a is None or b is None or b <= a:
        return ""
    return format_duration(max(1, round((b - a).days / 30.44)))


# How many offending ContractCommitmentIds a diagnostic lists inline.
_ID_SAMPLE_CAP = 25


def convert_contract_commitment(
    rows: list[dict[str, str]],
    *,
    service_provider_name: str,
    invoice_issuer_name: str,
    synthetic: bool = False,
    duration_lineages: list[Lineage] | None = None,
) -> list[dict[str, str]]:
    """Return the 13-column 1.3 ``rows`` expanded to the 1.4 30-column shape.

    ``ContractCommitmentDurationType`` is derived from a period spanning whole calendar
    months (``DERIVED``). Otherwise it is left empty (``UNAVAILABLE``), or in synthetic
    mode set to the nearest whole-month value (``ASSUMED``). The per-row lineage is appended
    to ``duration_lineages`` (aligned with the returned rows) for
    :func:`settle_duration_type`, which applies supplements and reports the outcome.
    """
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
            elif col == DURATION:
                start = row.get("ContractCommitmentPeriodStart", "")
                end = row.get("ContractCommitmentPeriodEnd", "")
                duration = _duration_type(start, end)
                lineage = Lineage.DERIVED
                if not duration:
                    duration = _rounded_duration(start, end) if synthetic else ""
                    lineage = Lineage.ASSUMED if duration else Lineage.UNAVAILABLE
                if duration_lineages is not None:
                    duration_lineages.append(lineage)
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
    return out


def settle_duration_type(
    rows: list[dict[str, str]],
    lineages: Sequence[Lineage],
    provenance: dict[str, ColumnRule],
    *,
    table: SupplementTable | None,
    synthetic: bool,
    counters: LineageCounters | None,
) -> tuple[dict[str, ColumnRule], Diagnostic | None]:
    """Apply supplied terms and settle ``ContractCommitmentDurationType`` per row.

    A supplied value always wins (``ENRICHED``). Otherwise the row keeps its own lineage from
    :func:`convert_contract_commitment`. Each value is counted exactly once in ``counters``
    (when the dataset keeps counters, i.e. supplements were given). The column rule is the
    weakest lineage present, so a single unprovable row in strict mode makes the column
    ``UNAVAILABLE``: Contract Commitment is then ``NOT_PRODUCED`` with the column listed as
    blocking, and the other datasets are unaffected. Returns the updated provenance and an
    ``FDT-CC-001`` warning for rows neither derivable nor supplied (or ``None``). Both
    pipelines call it at the same point, so outputs stay identical.
    """
    supplier = table if table is not None and DURATION in table.fact_columns else None
    final: list[Lineage] = []
    for row, lineage in zip(rows, lineages, strict=True):
        key = (row.get("ContractCommitmentId", ""),)
        supplied = supplier.value(key, DURATION) if supplier is not None else ""
        if supplied:
            row[DURATION] = supplied
            lineage = Lineage.ENRICHED
        final.append(lineage)
        if counters is not None:
            counters.record(DURATION, lineage)
    prov = dict(provenance)
    unresolved = [r.get("ContractCommitmentId", "") for r, lin in zip(rows, final, strict=True)
                  if lin in (Lineage.UNAVAILABLE, Lineage.ASSUMED)]
    if Lineage.UNAVAILABLE in final:
        prov[DURATION] = ColumnRule(
            Lineage.UNAVAILABLE,
            note=f"{final.count(Lineage.UNAVAILABLE)} commitment(s): the period spans no whole "
            "number of calendar months and no term was supplied",
        )
    elif Lineage.ASSUMED in final:
        prov[DURATION] = ColumnRule(
            Lineage.ASSUMED, "commitment period span",
            note="nearest whole-month value where the period spans no whole number of "
            "calendar months and no term was supplied",
        )
    elif Lineage.DERIVED in final:
        prov[DURATION] = PROVENANCE[DURATION]
    elif final and supplier is not None:
        prov[DURATION] = ColumnRule(Lineage.ENRICHED, supplier.source_for(DURATION))
    if not unresolved:
        return prov, None
    outcome = (
        "synthetic mode: the nearest whole-month value is assumed" if synthetic
        else "strict mode: Contract Commitment is not produced until the term is supplied"
    )
    return prov, Diagnostic(
        code="FDT-CC-001",
        severity=Severity.WARNING,
        message=(
            f"{len(unresolved)} commitment(s): ContractCommitmentDurationType cannot be "
            "derived (the period is unparseable, inverted or not a whole number of calendar "
            f"months) and was not supplied; {outcome}"
        ),
        datasets=(DATASET,),
        column=DURATION,
        context={
            "row_count": str(len(unresolved)),
            "contract_commitment_ids": ", ".join(sorted(set(unresolved))[:_ID_SAMPLE_CAP]),
        },
    )
