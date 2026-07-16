"""Convert FOCUS 1.2/1.3 Cost and Usage rows to the FOCUS 1.4 column set.

FOCUS 1.4 Cost and Usage keeps 1.3's 65-column count but:

* removes the deprecated ``ProviderName`` / ``PublisherName`` (superseded by
  the 1.3 ``ServiceProviderName`` / ``HostProviderName`` split);
* adds ``CommitmentProgramEligibilityDetails`` and ``InvoiceDetailId``
  (both conditional and nullable).

A 1.2 source is first lifted to the 1.3 shape: ``ServiceProviderName`` /
``HostProviderName`` are derived from ``ProviderName`` / ``PublisherName``,
and the 1.3-only columns (Split Cost Allocation set, ``ContractApplied``)
are null.
"""

from __future__ import annotations

from focus_data_toolkit.convert.contract_applied import migrate_1_3_to_1_4
from focus_data_toolkit.model import dataset_columns

DATASET = "Cost and Usage"

# 1.2 -> 1.3/1.4 provider-identity derivations.
_DERIVED_FROM_1_2 = {
    "ServiceProviderName": "ProviderName",
    "HostProviderName": "PublisherName",
}


def _convert_contract_applied(raw: str | None, source_version: str) -> str:
    """Migrate a source ``ContractApplied`` JSON to the FOCUS 1.4 schema.

    1.4 re-cases the identifier keys (``ContractID``->``ContractId``,
    ``ContractCommitmentID``->``ContractCommitmentId``). A 1.2 source has no
    ``ContractApplied`` column, so the value is empty there. Raises
    ``ContractAppliedError`` (a ``ValueError``) on a structurally invalid source value.
    """
    text = (raw or "").strip()
    if not text or source_version != "1.3":
        return text
    return migrate_1_3_to_1_4(text)


def convert_cost_and_usage(
    rows: list[dict[str, str]],
    source_version: str,
    *,
    invoice_detail_ids: dict[tuple[str, str], str] | None = None,
) -> list[dict[str, str]]:
    """Return ``rows`` reshaped to the FOCUS 1.4 Cost and Usage column set.

    ``invoice_detail_ids`` maps ``(InvoiceId, ChargeCategory)`` to the
    ``InvoiceDetailId`` assigned by the Invoice Detail builder, so converted
    rows link back to their invoice line item.
    """
    target = dataset_columns(DATASET)
    ids = invoice_detail_ids or {}
    out: list[dict[str, str]] = []
    for row in rows:
        converted: dict[str, str] = {}
        for col in target:
            if col == "ContractApplied":
                converted[col] = _convert_contract_applied(row.get(col), source_version)
            elif col in row:
                converted[col] = row[col]
            elif source_version == "1.2" and col in _DERIVED_FROM_1_2:
                converted[col] = row.get(_DERIVED_FROM_1_2[col], "")
            elif col == "InvoiceDetailId":
                # Key must match the Invoice Detail builder's (stripped) grouping key.
                key = ((row.get("InvoiceId") or "").strip(), (row.get("ChargeCategory") or "").strip())
                converted[col] = ids.get(key, "")
            else:
                # New-in-1.4 or 1.3-only columns absent from the source: null.
                converted[col] = ""
        # FOCUS 1.4 makes the pricing-currency pair non-nullable. When a 1.x
        # source leaves it null (e.g. tax or credit rows), pricing happened in
        # the billing currency, so backfill from the billing-currency values.
        if not converted.get("PricingCurrency"):
            converted["PricingCurrency"] = converted.get("BillingCurrency", "")
        if not converted.get("PricingCurrencyEffectiveCost"):
            converted["PricingCurrencyEffectiveCost"] = converted.get("EffectiveCost", "")
        out.append(converted)
    return out
