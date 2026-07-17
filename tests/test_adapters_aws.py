"""Provider-native adapters (PR-9a): AWS invoice-summary and savings-plans translation.

All fixture values are synthetic; the *field names* mirror the documented AWS export
shapes (botocore invoicing 2024-12-01, savingsplans 2019-06-28).
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from focus_data_toolkit.convert import convert_to_focus_1_4
from focus_data_toolkit.modes import Mode
from focus_data_toolkit.supplement import (
    SupplementBundle,
    SupplementError,
    SupplementFileSpec,
)
from focus_data_toolkit.supplement.adapters import (
    adapter_provenance,
    load_adapters,
)
from focus_data_toolkit.supplement.adapters.registry import (
    ADAPTERS_DIR,
    PROVENANCE_FILENAME,
    _to_utc_datetime,
)

P1, P2 = "2026-05-01T00:00:00Z", "2026-06-01T00:00:00Z"


def write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


# --------------------------------------------------------------------------- #
# Provenance manifest
# --------------------------------------------------------------------------- #
def test_adapter_provenance_hashes_match_files():
    manifest = adapter_provenance()
    listed = {a["file"] for a in manifest["adapters"]}
    on_disk = {p.name for p in ADAPTERS_DIR.glob("*.json") if p.name != PROVENANCE_FILENAME}
    assert listed == on_disk
    for entry in manifest["adapters"]:
        data = (ADAPTERS_DIR / entry["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["file"]
        assert entry["doc_url"].startswith("https://docs.aws.amazon.com/")


def test_all_adapters_declare_a_known_target_kind():
    from focus_data_toolkit.supplement.kinds import SUPPLEMENT_KINDS

    for adapter in load_adapters().values():
        assert adapter.target_kind in SUPPLEMENT_KINDS


# --------------------------------------------------------------------------- #
# date_to_utc transform
# --------------------------------------------------------------------------- #
def test_date_to_utc_normalizes_common_forms():
    assert _to_utc_datetime("2026-05-01") == "2026-05-01T00:00:00Z"
    assert _to_utc_datetime("2026-05-01T12:30:00") == "2026-05-01T12:30:00Z"
    assert _to_utc_datetime("2026-05-01T12:30:00Z") == "2026-05-01T12:30:00Z"
    assert _to_utc_datetime("2026-05-01T08:30:00-04:00") == "2026-05-01T12:30:00Z"
    # Unparseable is passed through (validation flags it), not guessed.
    assert _to_utc_datetime("last tuesday") == "last tuesday"


# --------------------------------------------------------------------------- #
# AWS invoice-summary adapter
# --------------------------------------------------------------------------- #
def aws_invoice_json_rows() -> list[dict]:
    # Shape of `aws invoicing list-invoice-summaries` output (nested Entity).
    return [
        {"InvoiceId": "INV-1", "IssuedDate": "2026-06-01T00:00:00Z",
         "DueDate": "2026-07-01T00:00:00Z",
         "Entity": {"InvoicingEntity": "AWS"},
         "InvoiceType": "INVOICE", "PurchaseOrderNumber": "PO-42"},
    ]


def test_aws_invoice_adapter_detected_and_translated(tmp_path):
    path = tmp_path / "aws_invoices.json"
    path.write_text(json.dumps(aws_invoice_json_rows()), encoding="utf-8")
    bundle = SupplementBundle.load([SupplementFileSpec(path=path)])
    table = bundle.get("invoice")
    assert table is not None
    assert table.adapter == "aws-invoice-summary@1"
    assert table.value(("AWS", "INV-1"), "InvoiceIssueStatus") == "Issued"
    assert table.value(("AWS", "INV-1"), "PaymentDueDate") == "2026-07-01T00:00:00Z"
    assert table.value(("AWS", "INV-1"), "PurchaseOrderNumber") == "PO-42"


def test_aws_invoice_adapter_forced_by_name(tmp_path):
    path = tmp_path / "x.json"
    path.write_text(json.dumps(aws_invoice_json_rows()), encoding="utf-8")
    bundle = SupplementBundle.load(
        [SupplementFileSpec(path=path, kind="aws-invoice-summary")]
    )
    assert bundle.get("invoice").adapter == "aws-invoice-summary@1"


def test_unknown_kind_or_adapter_errors(tmp_path):
    path = tmp_path / "x.json"
    path.write_text(json.dumps(aws_invoice_json_rows()), encoding="utf-8")
    with pytest.raises(SupplementError, match="unknown supplement kind/adapter"):
        SupplementBundle.load([SupplementFileSpec(path=path, kind="nope")])


# --------------------------------------------------------------------------- #
# AWS savings-plans adapter
# --------------------------------------------------------------------------- #
def aws_savings_plans_rows() -> list[dict[str, str]]:
    return [
        {"savingsPlanId": "sp-aaa", "paymentOption": "No Upfront", "state": "active",
         "savingsPlanType": "Compute", "start": "2026-05-01T00:00:00Z", "commitment": "1.5"},
        {"savingsPlanId": "sp-bbb", "paymentOption": "All Upfront", "state": "retired",
         "savingsPlanType": "EC2Instance", "start": "2025-05-01T00:00:00Z", "commitment": "3.0"},
    ]


def test_aws_savings_plans_adapter_maps_vocab(tmp_path):
    path = write_csv(tmp_path / "sp.csv", aws_savings_plans_rows())
    bundle = SupplementBundle.load([SupplementFileSpec(path=path)])
    table = bundle.get("contract_commitment")
    assert table.adapter == "aws-savings-plans@1"
    assert table.value(("sp-aaa",), "ContractCommitmentPaymentModel") == "No Upfront"
    assert table.value(("sp-aaa",), "ContractCommitmentPaymentInterval") == "Monthly"
    assert table.value(("sp-aaa",), "ContractCommitmentLifecycleStatus") == "Active"
    assert table.value(("sp-bbb",), "ContractCommitmentLifecycleStatus") == "Expired"
    assert table.value(("sp-bbb",), "ContractCommitmentPaymentInterval") == "One-Time"
    # Invariant product facts.
    assert table.value(("sp-aaa",), "ContractCommitmentBenefitCategory") == "Discount"
    assert table.value(("sp-aaa",), "ContractCommitmentModel") == "Continuous"
    assert table.value(("sp-aaa",), "ContractCommitmentFulfillmentInterval") == "Hourly"
    # LastUpdated / Applicability are NOT emitted (honest residual gaps).
    assert "ContractCommitmentLastUpdated" not in table.fact_columns
    assert "ContractCommitmentApplicability" not in table.fact_columns


def test_adapter_output_flows_through_validation_and_enriches(tmp_path, source_tables):
    # AWS invoice export + minimal PaymentTerms/status supplement -> Invoice Detail enriched.
    cau, _ = source_tables[("aws", "1.2")]
    seen = sorted({(r["InvoiceIssuerName"], r["InvoiceId"]) for r in cau if r.get("InvoiceId")})
    invoices = [
        {"InvoiceId": inv, "IssuedDate": "2026-06-01T00:00:00Z",
         "DueDate": "2026-07-01T00:00:00Z", "Entity": {"InvoicingEntity": issuer},
         "InvoiceType": "INVOICE", "PurchaseOrderNumber": "PO-1"}
        for issuer, inv in seen
    ]
    inv_path = tmp_path / "aws_invoices.json"
    inv_path.write_text(json.dumps(invoices), encoding="utf-8")
    bundle = SupplementBundle.load(
        [SupplementFileSpec(path=inv_path, provenance="aws invoicing api")]
    )
    result = convert_to_focus_1_4(cau, mode=Mode.SYNTHETIC, supplements=bundle)
    cols = result.manifest["datasets"]["Invoice Detail"]["columns"]
    assert cols["PaymentDueDate"]["lineage"] == "ENRICHED"
    assert cols["PaymentDueDate"]["source"] == "supplement:aws-invoice-summary@1:aws_invoices.json"
    supp = {e["kind"]: e for e in result.manifest["supplements"]}
    assert supp["invoice"]["adapter"] == "aws-invoice-summary@1"
    assert supp["invoice"]["provenance"] == "aws invoicing api"


def test_cli_supplements_adapters_lists_aws(capsys):
    from focus_data_toolkit.cli import main

    assert main(["supplements", "adapters"]) == 0
    out = capsys.readouterr().out
    assert "aws-invoice-summary" in out
    assert "aws-savings-plans" in out
