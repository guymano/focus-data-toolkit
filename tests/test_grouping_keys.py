"""Grouping-key grain and local-id stability/collision (P1.2)."""

from __future__ import annotations

from focus_data_toolkit.convert.invoice_detail import (
    GRAIN_FIELDS,
    invoice_detail_grain_key,
    invoice_detail_id,
)


def key(**over: str) -> tuple[str, ...]:
    base = {
        "InvoiceIssuerName": "AWS",
        "InvoiceId": "INV-1",
        "BillingAccountId": "BA-1",
        "BillingCurrency": "USD",
        "BillingPeriodStart": "2026-05-01T00:00:00Z",
        "BillingPeriodEnd": "2026-06-01T00:00:00Z",
        "ChargeCategory": "Usage",
    }
    base.update(over)
    return invoice_detail_grain_key(base)


def test_local_id_is_clearly_not_a_provider_id():
    detail_id = invoice_detail_id(key())
    assert detail_id.startswith("x_fdt_idl_v1_")


def test_id_is_stable_across_calls():
    assert invoice_detail_id(key()) == invoice_detail_id(key())


def test_id_changes_when_any_grain_field_changes():
    baseline = invoice_detail_id(key())
    for field in GRAIN_FIELDS:
        changed = invoice_detail_id(key(**{field: "DIFFERENT"}))
        assert changed != baseline, field


def test_grain_key_strips_whitespace():
    assert invoice_detail_grain_key({"InvoiceId": " INV-1 ", "ChargeCategory": "Usage "})[1] == "INV-1"


def test_delimiter_cannot_forge_a_collision():
    # ("A|B", "C") and ("A", "B|C") on adjacent fields must not hash to the same id.
    a = invoice_detail_id(key(InvoiceId="A|B", BillingAccountId="C"))
    b = invoice_detail_id(key(InvoiceId="A", BillingAccountId="B|C"))
    assert a != b


def test_same_invoice_different_issuer_distinct_ids():
    a = invoice_detail_id(key(InvoiceIssuerName="Provider A"))
    b = invoice_detail_id(key(InvoiceIssuerName="Provider B"))
    assert a != b


def test_same_invoice_different_currency_distinct_ids():
    assert invoice_detail_id(key(BillingCurrency="USD")) != invoice_detail_id(key(BillingCurrency="EUR"))
