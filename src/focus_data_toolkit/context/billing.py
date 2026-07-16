"""Billing context — issuer, account, currency and period, determined per row.

An export can consolidate many billing accounts, currencies, issuers and periods. Grouping
and enrichment must key on the billing context of each row, never on a single global value.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import astuple, dataclass


@dataclass(frozen=True)
class BillingContext:
    """The billing identity of a charge line."""

    invoice_issuer_name: str
    billing_account_id: str
    billing_currency: str
    billing_period_start: str
    billing_period_end: str

    @property
    def is_complete(self) -> bool:
        return all(astuple(self))

    def as_dict(self) -> dict[str, str]:
        return {
            "invoice_issuer_name": self.invoice_issuer_name,
            "billing_account_id": self.billing_account_id,
            "billing_currency": self.billing_currency,
            "billing_period_start": self.billing_period_start,
            "billing_period_end": self.billing_period_end,
        }


def billing_context_of_row(row: Mapping[str, str]) -> BillingContext:
    """Derive the billing context of a single row (empty where a field is absent)."""
    return BillingContext(
        (row.get("InvoiceIssuerName") or "").strip(),
        (row.get("BillingAccountId") or "").strip(),
        (row.get("BillingCurrency") or "").strip(),
        (row.get("BillingPeriodStart") or "").strip(),
        (row.get("BillingPeriodEnd") or "").strip(),
    )


def distinct_billing_contexts(rows: Iterable[Mapping[str, str]]) -> list[BillingContext]:
    """Return the distinct billing contexts across ``rows`` (deterministically ordered)."""
    seen: dict[tuple[str, ...], BillingContext] = {}
    for row in rows:
        ctx = billing_context_of_row(row)
        seen[astuple(ctx)] = ctx
    return [seen[key] for key in sorted(seen)]
