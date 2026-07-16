"""Inter-dataset (bundle) validation (P1.4). Hand-authored bundles."""

from __future__ import annotations

import json
from decimal import Decimal

from focus_data_toolkit.validate import validate_dataset_bundle

P1, P2 = "2026-05-01T00:00:00Z", "2026-06-01T00:00:00Z"


def cu(**over: str) -> dict[str, str]:
    base = {
        "InvoiceIssuerName": "AWS",
        "InvoiceId": "INV-1",
        "BillingAccountId": "BA-1",
        "BillingCurrency": "USD",
        "BillingPeriodStart": P1,
        "BillingPeriodEnd": P2,
        "ChargeCategory": "Usage",
        "BilledCost": "10.00",
        "InvoiceDetailId": "x_fdt_idl_v1_deadbeefdeadbeef",
    }
    base.update(over)
    return base


def invd(**over: str) -> dict[str, str]:
    base = {
        "InvoiceDetailId": "x_fdt_idl_v1_deadbeefdeadbeef",
        "InvoiceId": "INV-1",
        "ChargeCategory": "Usage",
        "BillingAccountId": "BA-1",
        "BillingCurrency": "USD",
        "BillingPeriodStart": P1,
        "BillingPeriodEnd": P2,
        "InvoiceIssuerName": "AWS",
        "BilledCost": "10.000000",
    }
    base.update(over)
    return base


def bp(**over: str) -> dict[str, str]:
    base = {"BillingPeriodStart": P1, "BillingPeriodEnd": P2, "InvoiceIssuerName": "AWS"}
    base.update(over)
    return base


def contract_applied(commitment_id: str) -> str:
    return (
        '{"Elements":[{"ContractId":"C1","ContractCommitmentId":"'
        + commitment_id
        + '","ContractCommitmentAppliedCost":1.0}]}'
    )


def codes(report) -> set[str]:
    return {d.code for d in report.diagnostics}


def test_clean_bundle_is_ok():
    report = validate_dataset_bundle({"Cost and Usage": [cu()], "Invoice Detail": [invd()], "Billing Period": [bp()]})
    assert report.ok
    assert not report.errors


def test_orphan_invoice_detail_id():
    report = validate_dataset_bundle(
        {"Cost and Usage": [cu(InvoiceDetailId="x_fdt_idl_v1_missing")], "Invoice Detail": [invd()]}
    )
    assert not report.ok
    assert "FDT-CROSS-014" in codes(report)


def test_duplicate_invoice_detail_id():
    report = validate_dataset_bundle({"Invoice Detail": [invd(), invd()]})
    assert "FDT-CROSS-001" in codes(report)


def test_currency_mismatch_between_datasets():
    report = validate_dataset_bundle(
        {"Cost and Usage": [cu(BillingCurrency="EUR")], "Invoice Detail": [invd(BillingCurrency="USD")]}
    )
    assert "FDT-CROSS-020" in codes(report)


def test_issuer_mismatch_between_datasets():
    report = validate_dataset_bundle(
        {"Cost and Usage": [cu(InvoiceIssuerName="Reseller")], "Invoice Detail": [invd(InvoiceIssuerName="AWS")]}
    )
    assert "FDT-CROSS-022" in codes(report)


def test_billing_period_not_covered():
    report = validate_dataset_bundle(
        {"Cost and Usage": [cu()], "Billing Period": [bp(BillingPeriodStart="2026-01-01T00:00:00Z")]}
    )
    assert "FDT-CROSS-040" in codes(report)


def test_contract_applied_orphan_reference():
    report = validate_dataset_bundle(
        {
            "Cost and Usage": [cu(ContractApplied=contract_applied("CM-MISSING"))],
            "Contract Commitment": [{"ContractCommitmentId": "CM-1"}],
        }
    )
    assert "FDT-CROSS-010" in codes(report)


def test_contract_applied_valid_reference_ok():
    report = validate_dataset_bundle(
        {
            "Cost and Usage": [cu(ContractApplied=contract_applied("CM-1"))],
            "Contract Commitment": [{"ContractCommitmentId": "CM-1"}],
        }
    )
    assert "FDT-CROSS-010" not in codes(report)


def test_wrong_invoice_line_is_flagged():
    # Same amount, but the linked Invoice Detail belongs to a different invoice/category.
    report = validate_dataset_bundle(
        {
            "Cost and Usage": [cu(InvoiceId="INV-1", ChargeCategory="Usage")],
            "Invoice Detail": [invd(InvoiceId="INV-999", ChargeCategory="Tax")],
        }
    )
    assert "FDT-CROSS-015" in codes(report)


def test_correction_self_reference_is_rejected():
    # A correction whose x_CorrectionOf points at its own key, with no surviving original.
    report = validate_dataset_bundle(
        {"Cost and Usage": [cu(ChargeClass="Correction", x_ChargeKey="k1", x_CorrectionOf="k1")]}
    )
    assert "FDT-CORR-001" in codes(report)


def test_non_finite_billed_cost_does_not_crash():
    # NaN parses as a Decimal but must not crash reconciliation.
    bundle = {
        "Cost and Usage": [cu(BilledCost="NaN", InvoiceDetailId="A")],
        "Invoice Detail": [invd(InvoiceDetailId="A", BilledCost="10.00")],
    }
    report = validate_dataset_bundle(bundle, invoice_detail_authoritative=True)
    assert isinstance(report.ok, bool)  # completed without raising


def test_reconciliation_runs_only_when_authoritative():
    bundle = {"Cost and Usage": [cu(BilledCost="10.00")], "Invoice Detail": [invd(BilledCost="999.00")]}
    # Derived (default): reconciliation skipped -> not applicable, still ok.
    assert validate_dataset_bundle(bundle).ok
    # Authoritative: the mismatch is now an error.
    report = validate_dataset_bundle(bundle, invoice_detail_authoritative=True)
    assert not report.ok
    assert "FDT-CROSS-030" in codes(report)


def test_reconciliation_within_tolerance_ok():
    bundle = {
        "Cost and Usage": [cu(BilledCost="10.00"), cu(BilledCost="0.005", InvoiceDetailId="x_fdt_idl_v1_deadbeefdeadbeef")],
        "Invoice Detail": [invd(BilledCost="10.00")],
    }
    report = validate_dataset_bundle(
        bundle, invoice_detail_authoritative=True, rounding_tolerance=Decimal("0.01")
    )
    assert "FDT-CROSS-030" not in codes(report)


def test_unmatched_invoice_line_warns():
    # Line "A" is covered by a Cost and Usage row; line "B" has none -> warning on B.
    bundle = {
        "Cost and Usage": [cu(InvoiceDetailId="A", BilledCost="10.00")],
        "Invoice Detail": [invd(InvoiceDetailId="A", BilledCost="10.00"), invd(InvoiceDetailId="B")],
    }
    report = validate_dataset_bundle(bundle, invoice_detail_authoritative=True)
    assert "FDT-CROSS-031" in codes(report)
    assert report.ok  # a warning is not a failure


def test_contract_commitment_period_inverted():
    report = validate_dataset_bundle(
        {
            "Contract Commitment": [
                {
                    "ContractCommitmentId": "CM-1",
                    "ContractCommitmentPeriodStart": P2,
                    "ContractCommitmentPeriodEnd": P1,
                }
            ]
        }
    )
    assert "FDT-CROSS-050" in codes(report)


def test_contract_commitment_percentage_out_of_range():
    report = validate_dataset_bundle(
        {"Contract Commitment": [{"ContractCommitmentId": "CM-1", "ContractCommitmentDiscountPercentage": "1.5"}]}
    )
    assert "FDT-CROSS-051" in codes(report)


def test_correction_reference_missing_original():
    report = validate_dataset_bundle(
        {"Cost and Usage": [cu(ChargeClass="Correction", x_CorrectionOf="orig-key-1")]}
    )
    assert "FDT-CORR-001" in codes(report)


def test_correction_reference_present_ok():
    report = validate_dataset_bundle(
        {
            "Cost and Usage": [
                cu(x_ChargeKey="orig-key-1"),
                cu(ChargeClass="Correction", x_CorrectionOf="orig-key-1", BilledCost="-10.00"),
            ]
        }
    )
    assert "FDT-CORR-001" not in codes(report)


def test_zero_reconciliation_tolerance_is_respected():
    bundle = {
        "Cost and Usage": [cu(BilledCost="10.005", InvoiceDetailId="A")],
        "Invoice Detail": [invd(InvoiceDetailId="A", BilledCost="10.00")],
    }
    report = validate_dataset_bundle(
        bundle, invoice_detail_authoritative=True, rounding_tolerance=Decimal("0")
    )
    assert "FDT-CROSS-030" in codes(report)  # 0.005 exceeds a zero tolerance


def test_invoice_detail_fk_without_target_is_not_executable():
    report = validate_dataset_bundle({"Cost and Usage": [cu(InvoiceDetailId="x_fdt_idl_v1_abc")]})
    assert "FDT-BUNDLE-001" in codes(report)


def test_contract_applied_fk_without_target_is_not_executable():
    report = validate_dataset_bundle(
        {"Cost and Usage": [cu(InvoiceDetailId="", ContractApplied=contract_applied("CM-1"))]}
    )
    assert "FDT-BUNDLE-001" in codes(report)


def test_duplicate_contract_commitment_id():
    report = validate_dataset_bundle(
        {"Contract Commitment": [{"ContractCommitmentId": "CM-1"}, {"ContractCommitmentId": "CM-1"}]}
    )
    assert "FDT-CROSS-001" in codes(report)


def test_non_finite_percentage_does_not_crash():
    report = validate_dataset_bundle(
        {"Contract Commitment": [{"ContractCommitmentId": "CM-1", "ContractCommitmentDiscountPercentage": "NaN"}]}
    )
    assert isinstance(report.ok, bool)
    assert "FDT-CROSS-051" not in codes(report)


def test_naive_commitment_date_does_not_crash():
    report = validate_dataset_bundle(
        {
            "Contract Commitment": [
                {
                    "ContractCommitmentId": "CM-1",
                    "ContractCommitmentPeriodStart": "2026-06-01T00:00:00",  # naive (no Z)
                    "ContractCommitmentPeriodEnd": "2026-05-01T00:00:00Z",  # aware
                }
            ]
        }
    )
    assert isinstance(report.ok, bool)  # mixed naive/aware must not raise TypeError


def test_cross_015_is_error_severity():
    from focus_data_toolkit.errors import Severity
    from focus_data_toolkit.validate import codes as codes_mod

    assert codes_mod.default_severity("FDT-CROSS-015") is Severity.ERROR


def test_report_is_json_serialisable():
    report = validate_dataset_bundle({"Cost and Usage": [cu()], "Invoice Detail": [invd()]})
    payload = json.dumps(report.as_dict())
    assert '"ok"' in payload and '"checks_run"' in payload
