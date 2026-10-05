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


def test_all_adapters_registered():
    assert set(load_adapters()) == {
        "aws-invoice-summary", "aws-savings-plans", "azure-invoice",
        "azure-reservation-orders", "azure-savings-plan-orders", "gcp-compute-commitments",
    }


# --------------------------------------------------------------------------- #
# Azure commitment order adapters (REST shape: {"value": [...]} with nested properties)
# --------------------------------------------------------------------------- #
RI_ORDER = "/providers/Microsoft.Capacity/reservationOrders/1e6407ba-0000-4000-8000-00000000a001"
SP_ORDER = "/providers/microsoft.billingbenefits/savingsPlanOrders/20000000-0000-4000-8000-00000000b001"


def azure_reservation_orders_json() -> dict:
    def order(oid: str, term: str, plan: str, state: str) -> dict:
        return {"id": oid, "name": oid.rsplit("/", 1)[1], "type": "Microsoft.Capacity/reservationOrders",
                "properties": {"term": term, "billingPlan": plan, "provisioningState": state,
                               "originalQuantity": 2, "createdDateTime": "2026-04-30T21:22:56.8541664Z",
                               "benefitStartTime": "2026-05-01T00:00:00Z",
                               "reservations": [{"id": f"{oid}/reservations/r-1"}]}}
    return {"value": [
        order(RI_ORDER, "P1Y", "Upfront", "Succeeded"),
        order(RI_ORDER[:-4] + "a002", "P3Y", "Monthly", "Cancelled"),
        order(RI_ORDER[:-4] + "a003", "P5Y", "Monthly", "Failed"),
    ], "nextLink": None}


def azure_savings_plan_orders_json() -> dict:
    return {"value": [
        {"id": SP_ORDER, "name": SP_ORDER.rsplit("/", 1)[1],
         "properties": {"term": "P3Y", "billingPlan": "P1M", "provisioningState": "Succeeded",
                        "benefitStartTime": "2026-05-01T00:00:00Z",
                        "savingsPlans": [SP_ORDER.replace("microsoft.billingbenefits",
                                                          "Microsoft.BillingBenefits") + "/savingsPlans/s-1"]},
         "sku": {"name": "Compute_Savings_Plan"}},
        {"id": SP_ORDER[:-4] + "b002", "name": "b002",
         "properties": {"term": "P1Y", "provisioningState": "Expired", "savingsPlans": []}},
    ]}


def _load(tmp_path: Path, name: str, data: dict):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return SupplementBundle.load([SupplementFileSpec(path=path)]).get("contract_commitment")


def test_azure_reservation_orders_adapter(tmp_path):
    table = _load(tmp_path, "ri_orders.json", azure_reservation_orders_json())
    assert table.adapter == "azure-reservation-orders@1"
    key = (RI_ORDER.lower(),)  # ARM ids are case-insensitive: keyed lowercased
    assert table.value(key, "ContractCommitmentDurationType") == "12 Months"
    assert table.value(key, "ContractCommitmentPaymentModel") == "All Upfront"
    assert table.value(key, "ContractCommitmentPaymentInterval") == "One-Time"
    assert table.value(key, "ContractCommitmentLifecycleStatus") == "Active"
    assert table.value(key, "ContractCommitmentCreated") == "2026-04-30T21:22:56.854166Z"
    assert table.value(key, "ContractCommitmentBenefitCategory") == "Discount"
    assert table.value(key, "ContractCommitmentModel") == "Continuous"
    monthly = (RI_ORDER[:-4].lower() + "a002",)
    assert table.value(monthly, "ContractCommitmentDurationType") == "36 Months"
    assert table.value(monthly, "ContractCommitmentPaymentModel") == "No Upfront"
    assert table.value(monthly, "ContractCommitmentPaymentInterval") == "Monthly"
    assert table.value(monthly, "ContractCommitmentLifecycleStatus") == "Canceled"
    # 'Failed' has no FOCUS lifecycle value: left for the client, never guessed.
    failed = (RI_ORDER[:-4].lower() + "a003",)
    assert table.value(failed, "ContractCommitmentLifecycleStatus") == ""
    assert table.value(failed, "ContractCommitmentDurationType") == "60 Months"
    # Not determinable from an order: never emitted.
    for column in ("ContractCommitmentFulfillmentInterval", "ContractCommitmentOfferCategory",
                   "ContractCommitmentDiscountPercentage", "InvoiceIssuerName"):
        assert column not in table.fact_columns, column


def test_azure_savings_plan_orders_adapter(tmp_path):
    table = _load(tmp_path, "sp_orders.json", azure_savings_plan_orders_json())
    assert table.adapter == "azure-savings-plan-orders@1"
    key = (SP_ORDER.lower(),)
    assert table.value(key, "ContractCommitmentDurationType") == "36 Months"
    assert table.value(key, "ContractCommitmentPaymentModel") == "No Upfront"
    assert table.value(key, "ContractCommitmentPaymentInterval") == "Monthly"
    assert table.value(key, "ContractCommitmentLifecycleStatus") == "Active"
    # No billingPlan: the API says it is required only for monthly plans, but never that its
    # absence means upfront, so the payment terms are left for the client.
    upfront = (SP_ORDER[:-4].lower() + "b002",)
    assert table.value(upfront, "ContractCommitmentPaymentModel") == ""
    assert table.value(upfront, "ContractCommitmentLifecycleStatus") == "Expired"
    for column in ("ContractCommitmentFulfillmentInterval", "ContractCommitmentCreated"):
        assert column not in table.fact_columns, column


def test_azure_order_adapters_are_told_apart_by_header():
    ri = detect_adapter(["id", "name", "properties.term", "properties.originalQuantity",
                         "properties.reservations", "properties.billingPlan"])
    sp = detect_adapter(["id", "name", "properties.term", "properties.savingsPlans",
                         "properties.billingPlan"])
    assert ri is not None and ri.name == "azure-reservation-orders"
    assert sp is not None and sp.name == "azure-savings-plan-orders"


def test_azure_order_adapter_enriches_a_contract_commitment_keyed_by_order(tmp_path, source_tables):
    # SightPilot-style 1.3 Contract Commitment keyed by the lowercased order ARM id.
    cau, cc = source_tables[("azure", "1.3")]
    cc = [dict(r) for r in cc[:1]]
    cc[0]["ContractCommitmentId"] = RI_ORDER.lower()
    table_path = tmp_path / "ri_orders.json"
    table_path.write_text(json.dumps(azure_reservation_orders_json()), encoding="utf-8")
    bundle = SupplementBundle.load([SupplementFileSpec(path=table_path)])
    result = convert_to_focus_1_4(cau, cc, mode=Mode.SYNTHETIC, supplements=bundle)
    [row] = result.datasets["Contract Commitment"]
    assert row["ContractCommitmentPaymentModel"] == "All Upfront"
    assert row["ContractCommitmentDurationType"] == "12 Months"
    assert row["ContractCommitmentLifecycleStatus"] == "Active"


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
