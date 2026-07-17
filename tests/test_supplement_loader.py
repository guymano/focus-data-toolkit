"""Supplement bundle loading + cross-validation (FDT-SUPP diagnostics)."""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import pytest

from focus_data_toolkit.supplement import (
    SupplementBundle,
    SupplementError,
    SupplementFileSpec,
    load_bundle_dir,
    parse_supplement_arg,
    source_key_sets,
    validate_supplements,
)
from focus_data_toolkit.supplement.loader import detect_kind
from focus_data_toolkit.supplement.validate import coverage, has_blocking_errors

P1, P2 = "2026-05-01T00:00:00Z", "2026-06-01T00:00:00Z"


def cau(**over: str) -> dict[str, str]:
    base = {
        "ServiceProviderName": "AWS", "HostProviderName": "AWS", "InvoiceIssuerName": "AWS",
        "InvoiceId": "INV-1", "BillingAccountId": "BA-1", "BillingCurrency": "USD",
        "BillingPeriodStart": P1, "BillingPeriodEnd": P2, "ChargeCategory": "Usage",
        "BilledCost": "10.00", "EffectiveCost": "10.00",
    }
    base.update(over)
    return base


def write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def bp_supplement_row(**over: str) -> dict[str, str]:
    base = {
        "InvoiceIssuerName": "AWS", "BillingPeriodStart": P1, "BillingPeriodEnd": P2,
        "BillingPeriodCreated": P1, "BillingPeriodLastUpdated": P2,
        "BillingPeriodStatus": "Closed",
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
# Spec parsing + kind detection
# --------------------------------------------------------------------------- #
def test_parse_supplement_arg_with_and_without_kind():
    assert parse_supplement_arg("f.csv").kind is None
    spec = parse_supplement_arg("dir/f.csv:invoice")
    assert spec.kind == "invoice" and spec.path == Path("dir/f.csv")


def test_detect_kind_billing_period_and_ambiguity():
    assert detect_kind(list(bp_supplement_row())).name == "billing_period"
    with pytest.raises(SupplementError, match="no known kind"):
        detect_kind(["Foo", "Bar"])


def test_forced_unknown_kind_and_missing_join_key_error(tmp_path):
    path = write_csv(tmp_path / "s.csv", [bp_supplement_row()])
    with pytest.raises(SupplementError, match="unknown supplement kind"):
        SupplementBundle.load([SupplementFileSpec(path=path, kind="nope")])
    bad = write_csv(tmp_path / "bad.csv", [{"BillingPeriodStatus": "Closed"}])
    with pytest.raises(SupplementError, match="requires join key"):
        SupplementBundle.load([SupplementFileSpec(path=bad, kind="billing_period")])


def test_duplicate_kind_files_rejected(tmp_path):
    a = write_csv(tmp_path / "a.csv", [bp_supplement_row()])
    b = write_csv(tmp_path / "b.csv", [bp_supplement_row()])
    with pytest.raises(SupplementError, match="multiple supplement files of kind"):
        SupplementBundle.load([SupplementFileSpec(path=a), SupplementFileSpec(path=b)])


# --------------------------------------------------------------------------- #
# Formats: gzip CSV + JSON sidecar + bundle dir
# --------------------------------------------------------------------------- #
def test_gzip_csv_and_json_supplements(tmp_path):
    gz = tmp_path / "bp.csv.gz"
    header = list(bp_supplement_row())
    body = ",".join(header) + "\r\n" + ",".join(bp_supplement_row()[c] for c in header) + "\r\n"
    with gzip.open(gz, "wt", encoding="utf-8", newline="") as fh:
        fh.write(body)
    js = tmp_path / "inv.json"
    js.write_text(json.dumps([{
        "InvoiceIssuerName": "AWS", "InvoiceId": "INV-1",
        "InvoiceIssueStatus": "Issued", "PaymentTerms": "Net 30",
    }]), encoding="utf-8")
    bundle = SupplementBundle.load([SupplementFileSpec(path=gz), SupplementFileSpec(path=js)])
    assert set(bundle.tables) == {"billing_period", "invoice"}
    assert bundle.get("invoice").value(("AWS", "INV-1"), "PaymentTerms") == "Net 30"


def test_bundle_dir_manifest(tmp_path):
    write_csv(tmp_path / "bp.csv", [bp_supplement_row()])
    (tmp_path / "supplements.json").write_text(json.dumps({
        "supplement_format": "1",
        "files": [{"path": "bp.csv", "kind": "billing_period",
                   "provenance": "exported from billing portal", "as_of": "2026-06-30"}],
    }), encoding="utf-8")
    specs = load_bundle_dir(tmp_path)
    bundle = SupplementBundle.load(specs)
    entry = bundle.manifest_entries()[0]
    assert entry["kind"] == "billing_period"
    assert entry["provenance"] == "exported from billing portal"
    assert entry["as_of"] == "2026-06-30"
    assert entry["sha256"] and entry["row_count"] == 1


def test_bundle_dir_requires_manifest(tmp_path):
    with pytest.raises(SupplementError, match="supplements.json"):
        load_bundle_dir(tmp_path)


# --------------------------------------------------------------------------- #
# Validation diagnostics
# --------------------------------------------------------------------------- #
def _validate(tmp_path, supplement_rows, cau_rows=None, kind=None):
    path = write_csv(tmp_path / "s.csv", supplement_rows)
    bundle = SupplementBundle.load([SupplementFileSpec(path=path, kind=kind)])
    keys = source_key_sets(cau_rows if cau_rows is not None else [cau()])
    return validate_supplements(bundle, keys)


def test_duplicate_join_key_is_an_error(tmp_path):
    diags = _validate(tmp_path, [bp_supplement_row(), bp_supplement_row()])
    assert any(d.code == "FDT-SUPP-001" for d in diags)
    assert has_blocking_errors(diags)


def test_unknown_column_is_an_error(tmp_path):
    rows = [dict(bp_supplement_row(), BillingPeriodSatus="Closed")]  # typo, not x_
    diags = _validate(tmp_path, rows, kind="billing_period")
    assert any(d.code == "FDT-SUPP-003" for d in diags)


def test_x_prefixed_extra_column_is_fine(tmp_path):
    rows = [dict(bp_supplement_row(), x_Note="from portal")]
    diags = _validate(tmp_path, rows)
    assert not any(d.code == "FDT-SUPP-003" for d in diags)


def test_bad_allowed_value_is_an_error(tmp_path):
    diags = _validate(tmp_path, [bp_supplement_row(BillingPeriodStatus="Done")])
    bad = [d for d in diags if d.code == "FDT-SUPP-004"]
    assert bad and bad[0].context["column"] == "BillingPeriodStatus"


def test_bad_datetime_is_an_error(tmp_path):
    diags = _validate(tmp_path, [bp_supplement_row(BillingPeriodCreated="last tuesday")])
    assert any(d.code == "FDT-SUPP-004" for d in diags)


def test_orphan_rows_warn_but_do_not_block(tmp_path):
    extra = bp_supplement_row(BillingPeriodStart="2026-07-01T00:00:00Z",
                              BillingPeriodEnd="2026-08-01T00:00:00Z")
    diags = _validate(tmp_path, [bp_supplement_row(), extra])
    orphan = [d for d in diags if d.code == "FDT-SUPP-005"]
    assert orphan and not has_blocking_errors(diags)


def test_billed_cost_conflict_is_an_error(tmp_path):
    line = {
        "InvoiceIssuerName": "AWS", "InvoiceId": "INV-1", "BillingAccountId": "BA-1",
        "BillingCurrency": "USD", "BillingPeriodStart": P1, "BillingPeriodEnd": P2,
        "ChargeCategory": "Usage", "InvoiceDetailId": "ID-1", "BilledCost": "999.00",
    }
    diags = _validate(tmp_path, [line], cau_rows=[cau(), cau(BilledCost="5.00")])
    assert any(d.code == "FDT-SUPP-006" for d in diags)


def test_matching_billed_cost_reconciles(tmp_path):
    line = {
        "InvoiceIssuerName": "AWS", "InvoiceId": "INV-1", "BillingAccountId": "BA-1",
        "BillingCurrency": "USD", "BillingPeriodStart": P1, "BillingPeriodEnd": P2,
        "ChargeCategory": "Usage", "InvoiceDetailId": "ID-1", "BilledCost": "15.00",
    }
    diags = _validate(tmp_path, [line], cau_rows=[cau(), cau(BilledCost="5.00")])
    assert not any(d.code == "FDT-SUPP-006" for d in diags)


def test_partial_coverage_reports_fdt_supp_010(tmp_path):
    rows = [bp_supplement_row(BillingPeriodStatus="")]
    diags = _validate(tmp_path, rows)
    cov = [d for d in diags if d.code == "FDT-SUPP-010"]
    assert any(d.context["column"] == "BillingPeriodStatus" for d in cov)
    assert not has_blocking_errors(diags)


def test_full_coverage_has_no_010_for_that_column(tmp_path):
    diags = _validate(tmp_path, [bp_supplement_row()])
    assert not any(
        d.code == "FDT-SUPP-010" and d.context["column"] == "BillingPeriodStatus"
        for d in diags
    )


def test_coverage_helper_counts(tmp_path):
    path = write_csv(tmp_path / "s.csv", [bp_supplement_row()])
    bundle = SupplementBundle.load([SupplementFileSpec(path=path)])
    keys = source_key_sets([
        cau(),
        cau(BillingPeriodStart="2026-06-01T00:00:00Z", BillingPeriodEnd="2026-07-01T00:00:00Z"),
    ])
    cov = coverage(bundle.get("billing_period"), keys.billing_periods)
    assert cov["BillingPeriodStatus"].total_keys == 2
    assert cov["BillingPeriodStatus"].covered == 1
    assert cov["BillingPeriodStatus"].complete is False


# --------------------------------------------------------------------------- #
# CLI pre-flight
# --------------------------------------------------------------------------- #
def test_supplements_validate_cli(tmp_path):
    from focus_data_toolkit.cli import main

    src = write_csv(tmp_path / "cau.csv", [cau()])
    good = write_csv(tmp_path / "bp.csv", [bp_supplement_row()])
    assert main(["supplements", "validate", "--cost-and-usage", str(src),
                 "--supplement", str(good)]) == 0
    bad = write_csv(tmp_path / "bad.csv", [bp_supplement_row(BillingPeriodStatus="Done")])
    assert main(["supplements", "validate", "--cost-and-usage", str(src),
                 "--supplement", str(bad)]) == 1
