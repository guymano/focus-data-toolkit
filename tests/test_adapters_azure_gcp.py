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
from focus_data_toolkit.supplement.adapters.registry import near_miss_adapters


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
RI_BASE = "/providers/Microsoft.Capacity/reservationOrders/1e6407ba-0000-4000-8000-00000000"
SP_BASE = "/providers/microsoft.billingbenefits/savingsPlanOrders/20000000-0000-4000-8000-00000000"
NEXT_LINK = "https://management.azure.com/providers/Microsoft.Capacity/reservationOrders?$skiptoken=x"


def ri_order(n: int, *, term: str = "P1Y", plan: str = "Upfront",
             state: str = "Succeeded") -> dict:
    # ReservationOrderResponse (reservations.json 2022-11-01), as Microsoft's List sample.
    oid = f"{RI_BASE}{n:04d}"
    return {"id": oid, "name": oid.rsplit("/", 1)[1], "type": "Microsoft.Capacity/reservationOrders",
            "etag": 3,
            "properties": {"displayName": "VM_RI_04-30-2026_14-20", "term": term,
                           "billingPlan": plan, "provisioningState": state,
                           "originalQuantity": 2, "requestDateTime": "2026-04-30T21:20:11.0335512Z",
                           "createdDateTime": "2026-04-30T21:22:56.8541664Z",
                           "benefitStartTime": "2026-05-01T00:00:00Z",
                           "expiryDateTime": "2027-05-01T00:00:00Z",
                           "reservations": [{"id": f"{oid}/reservations/r-1"}]}}


def sp_order(n: int, *, term: str = "P3Y", plan: str | None = "P1M",
             state: str = "Succeeded") -> dict:
    # SavingsPlanOrderModel (billingbenefits.json 2022-11-01); savingsPlans is optional.
    oid = f"{SP_BASE}{n:04d}"
    properties = {"displayName": "Compute_SavingsPlan_10-05-2026", "term": term,
                  "provisioningState": state, "benefitStartTime": "2026-05-01T00:00:00Z",
                  "expiryDateTime": "2029-05-01T00:00:00Z",
                  "billingScopeId": "/subscriptions/00000000-0000-0000-0000-000000000000"}
    if plan is not None:
        properties["billingPlan"] = plan
    return {"id": oid, "name": oid.rsplit("/", 1)[1],
            "type": "microsoft.billingbenefits/savingsPlanOrders",
            "sku": {"name": "Compute_Savings_Plan"}, "properties": properties}


def _load(tmp_path: Path, name: str, orders: list[dict]):
    path = tmp_path / name
    path.write_text(json.dumps({"value": orders, "nextLink": NEXT_LINK}), encoding="utf-8")
    return SupplementBundle.load([SupplementFileSpec(path=path)]).get("contract_commitment")


def _mapped(table, orders: list[dict], column: str) -> list[str]:
    return [table.value((order["id"].lower(),), column) for order in orders]


# Every spec enum value, plus off-enum and differently cased values (the enums are
# modelAsString, so anything else may arrive and must be dropped, never guessed).
RI_TERMS = {"P1Y": "1 Year", "P3Y": "3 Years", "P5Y": "5 Years", "P2Y": "", "p1y": ""}
RI_PLANS = {"Upfront": ("All Upfront", "One-Time"), "Monthly": ("No Upfront", "Monthly"),
            "upfront": ("", ""), "Quarterly": ("", "")}
RI_STATES = {
    "Creating": "Pending", "PendingResourceHold": "Pending", "ConfirmedResourceHold": "Pending",
    "PendingBilling": "Pending", "ConfirmedBilling": "Pending", "Created": "Pending",
    "Cancelled": "Canceled", "Expired": "Expired",
    # Provisioning finished says nothing about the term (running, or an exhausted pool).
    "Succeeded": "",
    # No FOCUS lifecycle value.
    "BillingFailed": "", "Failed": "", "Split": "", "Merged": "",
    "expired": "", "Exhausted": "",
}
SP_TERMS = {"P1M": "1 Month", "P1Y": "1 Year", "P3Y": "3 Years", "P5Y": "5 Years", "P2Y": ""}
SP_PLANS = {"P1M": ("No Upfront", "Monthly"), None: ("", ""), "Upfront": ("", ""), "p1m": ("", "")}
SP_STATES = {
    "Creating": "Pending", "PendingBilling": "Pending", "ConfirmedBilling": "Pending",
    "Created": "Pending", "Cancelled": "Canceled", "Expired": "Expired",
    "Succeeded": "", "Failed": "", "cancelled": "",
}


def test_azure_reservation_order_terms(tmp_path):
    orders = [ri_order(n, term=t) for n, t in enumerate(RI_TERMS)]
    table = _load(tmp_path, "ri.json", orders)
    assert _mapped(table, orders, "ContractCommitmentDurationType") == list(RI_TERMS.values())


def test_azure_reservation_order_billing_plans(tmp_path):
    orders = [ri_order(n, plan=p) for n, p in enumerate(RI_PLANS)]
    table = _load(tmp_path, "ri.json", orders)
    assert _mapped(table, orders, "ContractCommitmentPaymentModel") == [
        model for model, _ in RI_PLANS.values()
    ]
    assert _mapped(table, orders, "ContractCommitmentPaymentInterval") == [
        interval for _, interval in RI_PLANS.values()
    ]


def test_azure_reservation_order_states(tmp_path):
    orders = [ri_order(n, state=s) for n, s in enumerate(RI_STATES)]
    table = _load(tmp_path, "ri.json", orders)
    assert _mapped(table, orders, "ContractCommitmentLifecycleStatus") == list(RI_STATES.values())


def test_azure_reservation_order_facts(tmp_path):
    table = _load(tmp_path, "ri_orders.json", [ri_order(1)])
    assert table.adapter == "azure-reservation-orders@1"
    key = (f"{RI_BASE}0001".lower(),)  # ARM ids are case-insensitive: keyed lowercased
    assert table.value(key, "ContractCommitmentCreated") == "2026-04-30T21:22:56.854166Z"
    assert table.value(key, "ContractCommitmentBenefitCategory") == "Discount"
    # Not determinable from an order: never emitted.
    for column in ("ContractCommitmentModel", "ContractCommitmentFulfillmentInterval",
                   "ContractCommitmentOfferCategory", "ContractCommitmentDiscountPercentage",
                   "ContractCommitmentLastUpdated", "ContractCommitmentApplicability",
                   "InvoiceIssuerName"):
        assert column not in table.fact_columns, column


def test_azure_savings_plan_order_terms(tmp_path):
    orders = [sp_order(n, term=t) for n, t in enumerate(SP_TERMS)]
    table = _load(tmp_path, "sp.json", orders)
    assert _mapped(table, orders, "ContractCommitmentDurationType") == list(SP_TERMS.values())


def test_azure_savings_plan_order_billing_plans(tmp_path):
    # No billingPlan: the API says it is required only for monthly plans, but never that its
    # absence means upfront, so the payment terms are left for the client.
    orders = [sp_order(n, plan=p) for n, p in enumerate(SP_PLANS)]
    table = _load(tmp_path, "sp.json", orders)
    assert _mapped(table, orders, "ContractCommitmentPaymentModel") == [
        model for model, _ in SP_PLANS.values()
    ]
    assert _mapped(table, orders, "ContractCommitmentPaymentInterval") == [
        interval for _, interval in SP_PLANS.values()
    ]


def test_azure_savings_plan_order_states(tmp_path):
    orders = [sp_order(n, state=s) for n, s in enumerate(SP_STATES)]
    table = _load(tmp_path, "sp.json", orders)
    assert _mapped(table, orders, "ContractCommitmentLifecycleStatus") == list(SP_STATES.values())


def test_azure_savings_plan_order_facts(tmp_path):
    table = _load(tmp_path, "sp_orders.json", [sp_order(1)])
    assert table.adapter == "azure-savings-plan-orders@1"
    key = (f"{SP_BASE}0001".lower(),)
    assert table.value(key, "ContractCommitmentBenefitCategory") == "Discount"
    # The commitment grain (Hourly or FullTerm) is on the plan, not the order; Created needs
    # systemData, which this response omits.
    for column in ("ContractCommitmentModel", "ContractCommitmentFulfillmentInterval",
                   "ContractCommitmentCreated", "ContractCommitmentOfferCategory",
                   "ContractCommitmentLastUpdated", "ContractCommitmentApplicability",
                   "InvoiceIssuerName"):
        assert column not in table.fact_columns, column


def test_azure_savings_plan_order_created_comes_from_system_data(tmp_path):
    # FOCUS 1.4: Created is the moment the record was instantiated, which ARM records in
    # systemData.createdAt (7-digit fractions are truncated to microseconds).
    order = {**sp_order(1), "systemData": {"createdAt": "2026-04-30T21:22:56.8541664Z",
                                            "createdByType": "User"}}
    table = _load(tmp_path, "sp_orders.json", [order])
    key = (f"{SP_BASE}0001".lower(),)
    assert table.value(key, "ContractCommitmentCreated") == "2026-04-30T21:22:56.854166Z"


def _header(record: dict) -> list[str]:
    from focus_data_toolkit.supplement.loader import _flatten

    return list(_flatten(record))


def test_azure_order_adapters_are_told_apart_by_header():
    assert detect_adapter(_header(ri_order(1))).name == "azure-reservation-orders"
    sp = sp_order(1)
    assert detect_adapter(_header(sp)).name == "azure-savings-plan-orders"
    # The optional savingsPlans list is not required.
    assert "properties.savingsPlans" not in _header(sp)
    with_plans = {**sp, "properties": {**sp["properties"], "savingsPlans": ["x"]}}
    assert detect_adapter(_header(with_plans)).name == "azure-savings-plan-orders"


def test_plan_and_reservation_level_lists_are_not_orders():
    # SavingsPlanModel and ReservationResponse also carry id, sku, term and billingPlan.
    plan = {"id": "/providers/Microsoft.BillingBenefits/savingsPlanOrders/o/savingsPlans/p",
            "sku": {"name": "Compute_Savings_Plan"},
            "properties": {"term": "P3Y", "billingPlan": "P1M", "provisioningState": "Succeeded",
                           "displayProvisioningState": "Succeeded", "appliedScopeType": "Shared",
                           "commitment": {"grain": "Hourly", "currencyCode": "USD", "amount": 0.1},
                           "benefitStartTime": "2026-05-01T00:00:00Z"}}
    reservation = {"id": "/providers/Microsoft.Capacity/reservationOrders/o/reservations/r",
                   "sku": {"name": "Standard_D2s_v3"},
                   "properties": {"reservedResourceType": "VirtualMachines", "quantity": 1,
                                  "term": "P1Y", "billingPlan": "Monthly",
                                  "provisioningState": "Succeeded",
                                  "benefitStartTime": "2026-05-01T00:00:00Z"}}
    assert detect_adapter(_header(plan)) is None
    assert detect_adapter(_header(reservation)) is None
    # Nor are they near misses of the reservation order adapter: the sku rules it out.
    assert near_miss_adapters(_header(plan)) == []
    assert near_miss_adapters(_header(reservation)) == []


def _commitment_source(source_tables, order_id: str) -> tuple[list, list]:
    # A 1.3 Contract Commitment keyed by the lowercased order ARM id; the period spans a
    # whole year, so the duration below is derived unless the adapter supplies it.
    cau, cc = source_tables[("azure", "1.3")]
    row = dict(cc[0])
    row["ContractCommitmentId"] = order_id.lower()
    return cau, [row]


def test_azure_reservation_order_enriches_end_to_end(tmp_path, source_tables):
    order = ri_order(1, term="P3Y", plan="Upfront", state="Cancelled")
    cau, cc = _commitment_source(source_tables, order["id"])
    path = tmp_path / "ri_orders.json"
    path.write_text(json.dumps({"value": [order]}), encoding="utf-8")
    bundle = SupplementBundle.load([SupplementFileSpec(path=path)])
    result = convert_to_focus_1_4(cau, cc, mode=Mode.SYNTHETIC, supplements=bundle)
    [row] = result.datasets["Contract Commitment"]
    # Each expected value differs from both the derived value and the synthetic default
    # (1 Year, No Upfront, Monthly, Active).
    assert row["ContractCommitmentDurationType"] == "3 Years"
    assert row["ContractCommitmentPaymentModel"] == "All Upfront"
    assert row["ContractCommitmentPaymentInterval"] == "One-Time"
    assert row["ContractCommitmentLifecycleStatus"] == "Canceled"
    columns = result.manifest["datasets"]["Contract Commitment"]["columns"]
    source = "supplement:azure-reservation-orders@1:ri_orders.json"
    for column in ("ContractCommitmentDurationType", "ContractCommitmentPaymentModel",
                   "ContractCommitmentPaymentInterval", "ContractCommitmentLifecycleStatus"):
        assert columns[column]["lineage"] == "ENRICHED", column
        assert columns[column]["source"] == source, column


def test_azure_savings_plan_order_with_client_terms_end_to_end(tmp_path, source_tables):
    # The order cannot tell the commitment grain; a client file supplies the model and the
    # fulfillment interval for the same key, without conflicting with the adapter.
    order = sp_order(1, term="P5Y", state="Expired")
    cau, cc = _commitment_source(source_tables, order["id"])
    orders = tmp_path / "sp_orders.json"
    orders.write_text(json.dumps({"value": [order]}), encoding="utf-8")
    terms = write_csv(tmp_path / "terms.csv", [{
        "ContractCommitmentId": order["id"].lower(),
        "ContractCommitmentModel": "Discontinuous",
        "ContractCommitmentFulfillmentInterval": "Full Period",
    }])
    bundle = SupplementBundle.load(
        [SupplementFileSpec(path=orders), SupplementFileSpec(path=terms)]
    )
    result = convert_to_focus_1_4(cau, cc, mode=Mode.SYNTHETIC, supplements=bundle)
    [row] = result.datasets["Contract Commitment"]
    assert row["ContractCommitmentDurationType"] == "5 Years"
    assert row["ContractCommitmentLifecycleStatus"] == "Expired"
    assert row["ContractCommitmentPaymentModel"] == "No Upfront"
    assert row["ContractCommitmentModel"] == "Discontinuous"
    assert row["ContractCommitmentFulfillmentInterval"] == "Full Period"
    columns = result.manifest["datasets"]["Contract Commitment"]["columns"]
    # "No Upfront" is also the synthetic default: the attribution shows the adapter set it.
    for column in ("ContractCommitmentDurationType", "ContractCommitmentPaymentModel",
                   "ContractCommitmentPaymentInterval", "ContractCommitmentLifecycleStatus"):
        assert columns[column]["source"] == (
            "supplement:azure-savings-plan-orders@1:sp_orders.json"
        ), column
    assert columns["ContractCommitmentModel"]["source"] == "supplement:contract_commitment:terms.csv"


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
