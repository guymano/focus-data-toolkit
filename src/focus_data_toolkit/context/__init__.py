"""Billing and provider context objects, determined per row (never from the first row)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from focus_data_toolkit.context.billing import (
    BillingContext,
    billing_context_of_row,
    distinct_billing_contexts,
)
from focus_data_toolkit.context.provider import (
    ProviderContext,
    distinct_provider_contexts,
    provider_context_of_row,
    representative_provider,
)

# Cap on how many distinct values a summary lists inline (keeps the manifest bounded).
_SAMPLE_CAP = 25


def _capped(values: list[str]) -> dict:
    return {"count": len(values), "sample": values[:_SAMPLE_CAP]}


def describe_source_contexts(
    rows: Iterable[Mapping[str, str]], source_version: str
) -> dict:
    """A bounded, JSON-serialisable summary of the contexts present in a source.

    Reports distinct providers, issuers, accounts, currencies and periods, and boolean
    ``multi_*`` flags — enough for the manifest to show that (e.g.) the source mixed two
    issuers and three currencies, without embedding the full cross-product.
    """
    rows = list(rows)
    providers = distinct_provider_contexts(rows, source_version)
    billing = distinct_billing_contexts(rows)

    issuers = sorted({b.invoice_issuer_name for b in billing if b.invoice_issuer_name})
    accounts = sorted({b.billing_account_id for b in billing if b.billing_account_id})
    currencies = sorted({b.billing_currency for b in billing if b.billing_currency})
    periods = sorted(
        {(b.billing_period_start, b.billing_period_end) for b in billing if b.billing_period_start}
    )

    return {
        "providers": {
            "count": len(providers),
            "sample": [p.as_dict() for p in providers[:_SAMPLE_CAP]],
        },
        "invoice_issuers": _capped(issuers),
        "billing_accounts": _capped(accounts),
        "billing_currencies": _capped(currencies),
        "billing_periods": {
            "count": len(periods),
            "sample": [list(p) for p in periods[:_SAMPLE_CAP]],
        },
        "multi_provider": len(providers) > 1,
        "multi_issuer": len(issuers) > 1,
        "multi_currency": len(currencies) > 1,
        "multi_period": len(periods) > 1,
    }


__all__ = [
    "BillingContext",
    "ProviderContext",
    "billing_context_of_row",
    "describe_source_contexts",
    "distinct_billing_contexts",
    "distinct_provider_contexts",
    "provider_context_of_row",
    "representative_provider",
]
