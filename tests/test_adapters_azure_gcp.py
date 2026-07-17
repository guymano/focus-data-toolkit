"""Provider-native adapters (PR-9b): Azure invoice and GCP Compute commitments.

Fixture values are synthetic; field names mirror the documented export shapes
(Azure Billing Invoices REST API 2024-04-01; GCP Compute Engine v1 Commitment).
"""

from __future__ import annotations

import csv
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
from focus_data_toolkit.supplement.adapters import detect_adapter, load_adapters


def write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_all_four_adapters_registered():
    assert set(load_adapters()) == {
        "aws-invoice-summary", "aws-savings-plans", "azure-invoice", "gcp-compute-commitments"
    }


# --------------------------------------------------------------------------- #
# Azure invoice adapter
# --------------------------------------------------------------------------- #
def azure_invoice_json() -> list[dict]:
    # Shape of `az billing invoice list -o json` (nested properties).
    return [
        {"name": "G0012345", "type": "Microsoft.Billing/invoices",
         "properties": {"invoiceDate": "2026-06-01T00:00:00Z", "dueDate": "2026-07-01T00:00:00Z",
                        "status": "Paid", "invoicePeriodStartDate": "2026-05-01",
                        "purchaseOrderNumber": "PO-77",
                        "billingProfileDisplayName": "Contoso Billing"}},
        {"name": "G0012346", "type": "Microsoft.Billing/invoices",
         "properties": {"invoiceDate": "2026-06-01T00:00:00Z", "dueDate": "2026-07-01T00:00:00Z",
                        "status": "Void", "invoicePeriodStartDate": "2026-05-01"}},
    ]


def test_azure_invoice_adapter_maps_status_and_issuer(tmp_path):
    path = tmp_path / "az_invoices.json"
    path.write_text(json.dumps(azure_invoice_json()), encoding="utf-8")
    bundle = SupplementBundle.load([SupplementFileSpec(path=path)])
    table = bundle.get("invoice")
    assert table.adapter == "azure-invoice@1"
    assert table.value(("Microsoft", "G0012345"), "InvoiceIssueStatus") == "Issued"
    assert table.value(("Microsoft", "G0012346"), "InvoiceIssueStatus") == "Voided"
    assert table.value(("Microsoft", "G0012345"), "PurchaseOrderNumber") == "PO-77"
    assert table.value(("Microsoft", "G0012345"), "InvoiceIssueDate") == "2026-06-01T00:00:00Z"


def test_azure_invoice_detected_by_header():
    a = detect_adapter(["name", "properties.status", "properties.invoiceDate",
                        "properties.dueDate"])
    assert a is not None and a.name == "azure-invoice"


# --------------------------------------------------------------------------- #
# GCP Compute commitments adapter
# --------------------------------------------------------------------------- #
def gcp_commitments_rows() -> list[dict[str, str]]:
    return [
        {"name": "commit-a", "plan": "TWELVE_MONTH", "status": "ACTIVE",
         "startTimestamp": "2026-05-01T00:00:00Z", "endTimestamp": "2027-05-01T00:00:00Z",
         "region": "us-central1"},
        {"name": "commit-b", "plan": "THIRTY_SIX_MONTH", "status": "EXPIRED",
         "startTimestamp": "2023-05-01T00:00:00Z", "endTimestamp": "2026-05-01T00:00:00Z",
         "region": "europe-west1"},
    ]


def test_gcp_commitments_adapter_maps_status_and_constants(tmp_path):
    path = write_csv(tmp_path / "gcp_commitments.csv", gcp_commitments_rows())
    bundle = SupplementBundle.load([SupplementFileSpec(path=path)])
    table = bundle.get("contract_commitment")
    assert table.adapter == "gcp-compute-commitments@1"
    assert table.value(("commit-a",), "ContractCommitmentLifecycleStatus") == "Active"
    assert table.value(("commit-b",), "ContractCommitmentLifecycleStatus") == "Expired"
    assert table.value(("commit-a",), "ContractCommitmentPaymentModel") == "No Upfront"
    assert table.value(("commit-a",), "ContractCommitmentPaymentInterval") == "Monthly"
    assert table.value(("commit-a",), "ContractCommitmentBenefitCategory") == "Discount"
    assert table.value(("commit-a",), "ContractCommitmentCreated") == "2026-05-01T00:00:00Z"
    assert "ContractCommitmentApplicability" not in table.fact_columns


def test_gcp_forced_by_name(tmp_path):
    path = write_csv(tmp_path / "x.csv", gcp_commitments_rows())
    bundle = SupplementBundle.load(
        [SupplementFileSpec(path=path, kind="gcp-compute-commitments")]
    )
    assert bundle.get("contract_commitment").adapter == "gcp-compute-commitments@1"


# --------------------------------------------------------------------------- #
# End-to-end: multi-provider adapters enrich a strict conversion
# --------------------------------------------------------------------------- #
def test_azure_invoice_adapter_enriches_invoice_detail(tmp_path, source_tables):
    cau, _ = source_tables[("azure", "1.3")]
    issuers = sorted({r["InvoiceIssuerName"] for r in cau if r.get("InvoiceId")})
    # The Azure adapter emits issuer "Microsoft"; only join when the source uses it.
    if issuers != ["Microsoft"]:
        cau = [dict(r, InvoiceIssuerName="Microsoft") for r in cau]
    seen = sorted({r["InvoiceId"] for r in cau if r.get("InvoiceId")})
    invoices = [
        {"name": inv, "properties": {"invoiceDate": "2026-06-01T00:00:00Z",
         "dueDate": "2026-07-01T00:00:00Z", "status": "Paid",
         "purchaseOrderNumber": "PO-1"}}
        for inv in seen
    ]
    path = tmp_path / "az.json"
    path.write_text(json.dumps(invoices), encoding="utf-8")
    bundle = SupplementBundle.load(
        [SupplementFileSpec(path=path, provenance="azure billing api")]
    )
    result = convert_to_focus_1_4(cau, source_version="1.3", mode=Mode.SYNTHETIC,
                                  supplements=bundle)
    cols = result.manifest["datasets"]["Invoice Detail"]["columns"]
    assert cols["PaymentDueDate"]["source"] == "supplement:azure-invoice@1:az.json"
    supp = {e["kind"]: e for e in result.manifest["supplements"]}
    assert supp["invoice"]["adapter"] == "azure-invoice@1"


def test_unrecognized_header_still_falls_back_to_error(tmp_path):
    path = write_csv(tmp_path / "junk.csv", [{"Foo": "1", "Bar": "2"}])
    with pytest.raises(SupplementError, match="matches no known kind"):
        SupplementBundle.load([SupplementFileSpec(path=path)])
