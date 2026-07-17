"""End-to-end supplement application: strict mode produces factual 1.4 datasets."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from focus_data_toolkit.convert import ConversionError, convert_to_focus_1_4
from focus_data_toolkit.manifest import render
from focus_data_toolkit.model import FOCUS_1_4_DATASETS
from focus_data_toolkit.modes import Mode
from focus_data_toolkit.supplement import SupplementBundle, SupplementFileSpec

P1, P2 = "2026-05-01T00:00:00Z", "2026-06-01T00:00:00Z"


def write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def source(source_tables):
    cau, _ = source_tables[("aws", "1.2")]
    return cau


@pytest.fixture
def cc_source(source_tables):
    _, cc = source_tables[("aws", "1.3")]
    return cc


def _billing_period_rows(cau) -> list[dict[str, str]]:
    keys = sorted({
        (r["InvoiceIssuerName"].strip(), r["BillingPeriodStart"].strip(),
         r["BillingPeriodEnd"].strip())
        for r in cau if r.get("BillingPeriodStart")
    })
    return [
        {"InvoiceIssuerName": i, "BillingPeriodStart": s, "BillingPeriodEnd": e,
         "BillingPeriodCreated": s, "BillingPeriodLastUpdated": e,
         "BillingPeriodStatus": "Closed"}
        for i, s, e in keys
    ]


def _invoice_rows(cau) -> list[dict[str, str]]:
    keys = sorted({
        (r["InvoiceIssuerName"].strip(), r["InvoiceId"].strip())
        for r in cau if r.get("InvoiceId")
    })
    return [
        {"InvoiceIssuerName": issuer, "InvoiceId": inv,
         "InvoiceIssueStatus": "Issued", "PaymentTerms": "Net 30",
         "ReferenceInvoiceId": inv, "InvoiceIssueDate": P2,
         "PaymentDueDate": "2026-07-01T00:00:00Z"}
        for issuer, inv in keys
    ]


def _invoice_line_rows(cau) -> list[dict[str, str]]:
    from focus_data_toolkit.convert.invoice_detail import GRAIN_FIELDS, invoice_detail_grain_key

    grains = sorted({invoice_detail_grain_key(r) for r in cau if (r.get("InvoiceId") or "").strip()})
    out = []
    for n, grain in enumerate(grains):
        row = dict(zip(GRAIN_FIELDS, grain, strict=True))
        row.update({
            "InvoiceDetailId": f"AWSLINE-{n:04d}",
            "InvoiceDetailCreated": grain[5] or P2,
            "InvoiceDetailLastUpdated": grain[5] or P2,
        })
        out.append(row)
    return out


def _cc_supplement_rows(cc) -> list[dict[str, str]]:
    applicability = json.dumps({"IsComplexScope": True, "x_Source": "contract records"},
                               separators=(",", ":"))
    return [
        {"ContractCommitmentId": r["ContractCommitmentId"],
         "ContractCommitmentCreated": r["ContractCommitmentPeriodStart"],
         "ContractCommitmentLastUpdated": r["ContractCommitmentPeriodStart"],
         "ContractCommitmentApplicability": applicability,
         "ContractCommitmentBenefitCategory": "Discount",
         "ContractCommitmentFulfillmentInterval": "Monthly",
         "ContractCommitmentLifecycleStatus": "Active",
         "ContractCommitmentModel": "Continuous",
         "ContractCommitmentOfferCategory": "Public",
         "ContractCommitmentPaymentInterval": "Monthly",
         "ContractCommitmentPaymentModel": "No Upfront"}
        for r in cc
    ]


@pytest.fixture
def full_bundle(tmp_path, source, cc_source) -> SupplementBundle:
    files = {
        "bp.csv": _billing_period_rows(source),
        "invoices.csv": _invoice_rows(source),
        "lines.csv": _invoice_line_rows(source),
        "commitments.csv": _cc_supplement_rows(cc_source),
    }
    specs = [
        SupplementFileSpec(path=write_csv(tmp_path / name, rows), provenance="client records")
        for name, rows in files.items()
    ]
    return SupplementBundle.load(specs)


def test_strict_with_full_supplements_produces_all_four_datasets(source, cc_source, full_bundle):
    result = convert_to_focus_1_4(
        source, cc_source, mode=Mode.STRICT, supplements=full_bundle
    )
    assert set(result.coverage) == set(FOCUS_1_4_DATASETS)
    assert result.ok, {
        n: r.messages()[:5] for n, r in result.reports.items() if not r.ok
    }
    for name in FOCUS_1_4_DATASETS:
        entry = result.manifest["datasets"][name]
        assert entry["status"] == "PRODUCED", name
        lineages = {c["lineage"] for c in entry["columns"].values()}
        assert "ASSUMED" not in lineages, name
    assert result.manifest["assumptions_present"] is False
    # Output filenames carry no synthetic_ prefix.
    assert all(
        not e.get("output_file", "").startswith("synthetic_")
        for e in result.manifest["datasets"].values()
    )


def test_enriched_columns_carry_supplement_attribution(source, cc_source, full_bundle):
    result = convert_to_focus_1_4(source, cc_source, mode=Mode.STRICT, supplements=full_bundle)
    bp = result.manifest["datasets"]["Billing Period"]["columns"]
    assert bp["BillingPeriodStatus"]["lineage"] == "ENRICHED"
    assert bp["BillingPeriodStatus"]["source"] == "supplement:billing_period:bp.csv"
    invd = result.manifest["datasets"]["Invoice Detail"]["columns"]
    assert invd["InvoiceDetailId"]["source"] == "supplement:invoice_line:lines.csv"
    # The manifest lists the supplement files with hashes.
    supp = {e["kind"]: e for e in result.manifest["supplements"]}
    assert set(supp) == {"billing_period", "invoice", "invoice_line", "contract_commitment"}
    assert all(e["sha256"] for e in supp.values())
    assert supp["billing_period"]["provenance"] == "client records"


def test_cost_and_usage_backlinks_use_real_ids(source, cc_source, full_bundle):
    result = convert_to_focus_1_4(source, cc_source, mode=Mode.STRICT, supplements=full_bundle)
    detail_ids = {r["InvoiceDetailId"] for r in result.datasets["Invoice Detail"]}
    assert all(i.startswith("AWSLINE-") for i in detail_ids)
    cu_ids = {r["InvoiceDetailId"] for r in result.datasets["Cost and Usage"] if r["InvoiceId"]}
    assert cu_ids <= detail_ids
    cu_prov = result.manifest["datasets"]["Cost and Usage"]["columns"]["InvoiceDetailId"]
    assert cu_prov["lineage"] == "ENRICHED"


def test_partial_coverage_keeps_dataset_blocked_in_strict(tmp_path, source):
    rows = _billing_period_rows(source)
    rows[0]["BillingPeriodStatus"] = ""  # one key uncovered
    bundle = SupplementBundle.load(
        [SupplementFileSpec(path=write_csv(tmp_path / "bp.csv", rows))]
    )
    result = convert_to_focus_1_4(source, mode=Mode.STRICT, supplements=bundle)
    entry = result.manifest["datasets"]["Billing Period"]
    assert entry["status"] == "NOT_PRODUCED"
    assert "BillingPeriodStatus" in entry["blocking_columns"]
    assert any(d.code == "FDT-SUPP-010" for d in result.diagnostics)


def test_conflicting_supplement_refuses_conversion(tmp_path, source):
    rows = _billing_period_rows(source)
    rows.append(dict(rows[0]))  # duplicate join key -> FDT-SUPP-001 ERROR
    bundle = SupplementBundle.load(
        [SupplementFileSpec(path=write_csv(tmp_path / "bp.csv", rows))]
    )
    with pytest.raises(ConversionError, match="FDT-SUPP-001"):
        convert_to_focus_1_4(source, mode=Mode.STRICT, supplements=bundle)


def test_strict_never_leaks_synthetic_defaults(tmp_path, source):
    # Only invoice-header + line supplements: Invoice Detail becomes producible, and its
    # nullable assumed columns (description, grain, issue date) must be empty, never the
    # synthetic defaults.
    specs = [
        SupplementFileSpec(path=write_csv(tmp_path / "inv.csv", _invoice_rows(source))),
        SupplementFileSpec(path=write_csv(tmp_path / "lines.csv", _invoice_line_rows(source))),
    ]
    result = convert_to_focus_1_4(
        source, mode=Mode.STRICT, supplements=SupplementBundle.load(specs)
    )
    details = result.datasets["Invoice Detail"]
    assert details
    assert all(r["InvoiceDetailDescription"] == "" for r in details)
    assert all(r["InvoiceDetailGrain"] == "" for r in details)
    cols = result.manifest["datasets"]["Invoice Detail"]["columns"]
    assert cols["InvoiceDetailDescription"]["lineage"] == "UNAVAILABLE"
    # Billing Period got no supplement: still NOT_PRODUCED.
    assert result.manifest["datasets"]["Billing Period"]["status"] == "NOT_PRODUCED"


def test_synthetic_mode_fills_gaps_with_defaults_and_counts_them(tmp_path, source):
    # Two billing periods; the supplement covers the status of only one of them.
    p3 = "2026-07-01T00:00:00Z"
    two_periods = [dict(r) for r in source] + [
        dict(source[0], BillingPeriodStart=P2, BillingPeriodEnd=p3)
    ]
    rows = _billing_period_rows(two_periods)
    assert len(rows) == 2
    rows[0]["BillingPeriodStatus"] = ""
    bundle = SupplementBundle.load(
        [SupplementFileSpec(path=write_csv(tmp_path / "bp.csv", rows))]
    )
    result = convert_to_focus_1_4(two_periods, mode=Mode.SYNTHETIC, supplements=bundle)
    entry = result.manifest["datasets"]["Billing Period"]
    assert entry["status"] == "PRODUCED_SYNTHETIC"
    summary = entry["lineage_summary"]["BillingPeriodStatus"]
    assert summary["ENRICHED"] == 1
    assert summary["ASSUMED"] == 1
    # Defaults still filled in synthetic mode.
    assert all(r["BillingPeriodStatus"] for r in result.datasets["Billing Period"])


def test_supplemented_conversion_is_deterministic(source, cc_source, full_bundle):
    a = convert_to_focus_1_4(source, cc_source, mode=Mode.STRICT,
                             supplements=full_bundle, validate=False)
    b = convert_to_focus_1_4(source, cc_source, mode=Mode.STRICT,
                             supplements=full_bundle, validate=False)
    assert a.datasets == b.datasets
    assert render(a.manifest) == render(b.manifest)


def test_without_supplements_strict_behavior_is_unchanged(source):
    result = convert_to_focus_1_4(source, mode=Mode.STRICT)
    assert result.coverage == ("Cost and Usage",)
    assert "supplements" not in result.manifest
