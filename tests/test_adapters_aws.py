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
        assert entry["doc_url"].startswith("https://")


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
ARN_A = "arn:aws:savingsplans::111122223333:savingsplan/0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
ARN_B = "arn:aws:savingsplans::111122223333:savingsplan/1b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5e"


def aws_savings_plans_rows() -> list[dict[str, str]]:
    return [
        {"savingsPlanId": ARN_A.rsplit("/", 1)[1], "savingsPlanArn": ARN_A,
         "paymentOption": "No Upfront", "state": "active", "savingsPlanType": "Compute",
         "start": "2026-05-01T00:00:00Z", "commitment": "1.5", "termDurationInSeconds": "31536000"},
        {"savingsPlanId": ARN_B.rsplit("/", 1)[1], "savingsPlanArn": ARN_B,
         "paymentOption": "All Upfront", "state": "retired", "savingsPlanType": "EC2Instance",
         "start": "2025-05-01T00:00:00Z", "commitment": "3.0", "termDurationInSeconds": "94608000"},
    ]


def test_aws_savings_plans_adapter_maps_vocab(tmp_path):
    path = write_csv(tmp_path / "sp.csv", aws_savings_plans_rows())
    bundle = SupplementBundle.load([SupplementFileSpec(path=path)])
    table = bundle.get("contract_commitment")
    assert table.adapter == "aws-savings-plans@2"
    # Keyed by the fully qualified ARN, never the bare id.
    assert table.value((ARN_A,), "ContractCommitmentPaymentModel") == "No Upfront"
    assert table.value((ARN_A,), "ContractCommitmentPaymentInterval") == "Monthly"
    assert table.value((ARN_A,), "ContractCommitmentLifecycleStatus") == "Active"
    assert table.value((ARN_B,), "ContractCommitmentLifecycleStatus") == "Expired"
    assert table.value((ARN_B,), "ContractCommitmentPaymentInterval") == "One-Time"
    assert table.value((ARN_A,), "ContractCommitmentDurationType") == "1 Year"
    assert table.value((ARN_B,), "ContractCommitmentDurationType") == "3 Years"
    # Invariant product facts.
    assert table.value((ARN_A,), "ContractCommitmentBenefitCategory") == "Discount"
    assert table.value((ARN_A,), "ContractCommitmentModel") == "Continuous"
    assert table.value((ARN_A,), "ContractCommitmentFulfillmentInterval") == "Hourly"
    # LastUpdated / Applicability are NOT emitted (honest residual gaps).
    assert "ContractCommitmentLastUpdated" not in table.fact_columns
    assert "ContractCommitmentApplicability" not in table.fact_columns


def test_aws_savings_plans_unknown_term_is_left_for_the_client(tmp_path):
    rows = aws_savings_plans_rows()
    rows[0]["termDurationInSeconds"] = "63072000"  # not an offered term: never guessed
    table = SupplementBundle.load(
        [SupplementFileSpec(path=write_csv(tmp_path / "sp.csv", rows))]
    ).get("contract_commitment")
    assert table.value((ARN_A,), "ContractCommitmentDurationType") == ""
    assert table.value((ARN_B,), "ContractCommitmentDurationType") == "3 Years"


def test_aws_savings_plans_export_without_arn_is_not_auto_detected(tmp_path):
    # A v1-shaped export (bare savingsPlanId only) cannot join an ARN-keyed Contract
    # Commitment, so the adapter does not claim it, and the error names the missing field.
    rows = [{k: v for k, v in r.items() if k != "savingsPlanArn"} for r in aws_savings_plans_rows()]
    with pytest.raises(SupplementError, match="aws-savings-plans@2 requires savingsPlanArn"):
        SupplementBundle.load([SupplementFileSpec(path=write_csv(tmp_path / "sp.csv", rows))])


def test_aws_savings_plans_join_the_contract_commitment_end_to_end(tmp_path, source_tables):
    # The generated 1.3 Contract Commitment is keyed by ARN, like CommitmentDiscountId.
    cau, cc = source_tables[("aws", "1.3")]
    spend = [r for r in cc if ":savingsplan/" in r["ContractCommitmentId"]]
    assert spend, "the AWS 1.3 sample carries Savings Plans commitments"
    rows = [
        {"savingsPlanId": r["ContractCommitmentId"].rsplit("/", 1)[1],
         "savingsPlanArn": r["ContractCommitmentId"], "paymentOption": "No Upfront",
         "state": "active", "start": r["ContractCommitmentPeriodStart"],
         "termDurationInSeconds": "31536000"}
        for r in spend
    ]
    bundle = SupplementBundle.load([SupplementFileSpec(path=write_csv(tmp_path / "sp.csv", rows))])
    result = convert_to_focus_1_4(cau, cc, mode=Mode.SYNTHETIC, supplements=bundle)
    orphans = [d for d in result.diagnostics if d.code == "FDT-SUPP-005"]
    assert not orphans, [d.message for d in orphans]
    by_id = {r["ContractCommitmentId"]: r for r in result.datasets["Contract Commitment"]}
    for r in spend:
        enriched = by_id[r["ContractCommitmentId"]]
        assert enriched["ContractCommitmentPaymentModel"] == "No Upfront"
        assert enriched["ContractCommitmentDurationType"] == "1 Year"
    summary = result.manifest["datasets"]["Contract Commitment"]["lineage_summary"]
    assert summary["ContractCommitmentPaymentModel"]["ENRICHED"] == len(spend)


def _describe_savings_plans(spend: list[dict[str, str]]) -> dict:
    # The native `aws savingsplans describe-savings-plans` envelope: numbers stay JSON
    # numbers, timestamps carry milliseconds, and tags / productTypes are nested.
    return {
        "savingsPlans": [
            {"savingsPlanId": r["ContractCommitmentId"].rsplit("/", 1)[1],
             "savingsPlanArn": r["ContractCommitmentId"],
             "description": "1 year No Upfront Compute Savings Plan",
             "start": r["ContractCommitmentPeriodStart"].replace("Z", ".000Z"),
             "end": r["ContractCommitmentPeriodEnd"].replace("Z", ".000Z"),
             "state": "active", "region": "us-east-1", "savingsPlanType": "Compute",
             "paymentOption": "No Upfront", "productTypes": ["EC2", "Fargate", "Lambda"],
             "currency": "USD", "commitment": "1.5", "upfrontPaymentAmount": "0.0",
             "recurringPaymentAmount": "1.5", "termDurationInSeconds": 31536000,
             "tags": {"team": "platform"}}
            for r in spend
        ],
        "nextToken": "",
    }


def test_aws_native_describe_savings_plans_output_joins(tmp_path, source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    spend = [r for r in cc if ":savingsplan/" in r["ContractCommitmentId"]]
    path = tmp_path / "describe_savings_plans.json"
    path.write_text(json.dumps(_describe_savings_plans(spend)), encoding="utf-8")
    bundle = SupplementBundle.load([SupplementFileSpec(path=path)])
    assert bundle.get("contract_commitment").adapter == "aws-savings-plans@2"
    result = convert_to_focus_1_4(cau, cc, mode=Mode.SYNTHETIC, supplements=bundle)
    assert not [d for d in result.diagnostics if d.code == "FDT-SUPP-005"]
    by_id = {r["ContractCommitmentId"]: r for r in result.datasets["Contract Commitment"]}
    for r in spend:
        enriched = by_id[r["ContractCommitmentId"]]
        assert enriched["ContractCommitmentDurationType"] == "1 Year"
        assert enriched["ContractCommitmentCreated"] == r["ContractCommitmentPeriodStart"]
    summary = result.manifest["datasets"]["Contract Commitment"]["lineage_summary"]
    assert summary["ContractCommitmentDurationType"]["ENRICHED"] == len(spend)


def test_bare_savings_plan_ids_never_join(tmp_path, source_tables):
    # A supplement keyed by the bare id matches no ARN-keyed commitment: every row is an
    # orphan, reported, and nothing is enriched.
    cau, cc = source_tables[("aws", "1.3")]
    spend = [r for r in cc if ":savingsplan/" in r["ContractCommitmentId"]]
    rows = [
        {"ContractCommitmentId": r["ContractCommitmentId"].rsplit("/", 1)[1],
         "ContractCommitmentPaymentModel": "All Upfront"}
        for r in spend
    ]
    bundle = SupplementBundle.load([SupplementFileSpec(path=write_csv(tmp_path / "cc.csv", rows))])
    result = convert_to_focus_1_4(cau, cc, mode=Mode.SYNTHETIC, supplements=bundle)
    [orphans] = [d for d in result.diagnostics if d.code == "FDT-SUPP-005"]
    assert str(len(spend)) in orphans.message
    assert all(
        r["ContractCommitmentPaymentModel"] != "All Upfront"
        for r in result.datasets["Contract Commitment"]
    )


def test_independent_savings_plans_export_joins_an_arn_keyed_commitment(tmp_path, source_tables):
    # The export rows are written by hand (aws_savings_plans_rows, realistic ARNs); only the
    # Contract Commitment source is rekeyed to those ARNs, so the supplement is never built
    # from the ids it must join.
    cau, cc = source_tables[("aws", "1.3")]
    commitments = [dict(r) for r in cc if ":savingsplan/" in r["ContractCommitmentId"]][:2]
    commitments[0]["ContractCommitmentId"] = ARN_A
    commitments[1]["ContractCommitmentId"] = ARN_B
    export = write_csv(tmp_path / "sp.csv", aws_savings_plans_rows())
    bundle = SupplementBundle.load([SupplementFileSpec(path=export)])
    result = convert_to_focus_1_4(cau, commitments, mode=Mode.SYNTHETIC, supplements=bundle)
    assert not [d for d in result.diagnostics if d.code == "FDT-SUPP-005"]
    by_id = {r["ContractCommitmentId"]: r for r in result.datasets["Contract Commitment"]}
    assert by_id[ARN_A]["ContractCommitmentPaymentModel"] == "No Upfront"
    assert by_id[ARN_A]["ContractCommitmentLifecycleStatus"] == "Active"
    assert by_id[ARN_B]["ContractCommitmentPaymentModel"] == "All Upfront"
    assert by_id[ARN_B]["ContractCommitmentPaymentInterval"] == "One-Time"
    assert by_id[ARN_B]["ContractCommitmentLifecycleStatus"] == "Expired"
    # The supplied 3-year term wins over the 1 Year the 12-month period would derive.
    assert by_id[ARN_B]["ContractCommitmentDurationType"] == "3 Years"


def test_malformed_canonical_file_gets_the_canonical_error_not_an_adapter_hint(tmp_path):
    # InvoiceId and PurchaseOrderNumber are FOCUS names the AWS invoice export shares: a
    # FOCUS-named invoice file missing InvoiceIssuerName is a malformed canonical file.
    path = write_csv(tmp_path / "inv.csv", [{"InvoiceId": "INV-1", "PurchaseOrderNumber": "PO-1"}])
    with pytest.raises(SupplementError, match="need all join keys") as exc:
        SupplementBundle.load([SupplementFileSpec(path=path)])
    assert "aws-invoice-summary" not in str(exc.value)


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
