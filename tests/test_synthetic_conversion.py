"""Synthetic-mode invariants: assumptions are generated, but always labelled."""

from __future__ import annotations

import pytest

from focus_data_toolkit.convert import convert_to_focus_1_4
from focus_data_toolkit.modes import Mode


@pytest.fixture()
def synthetic_result(source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    return convert_to_focus_1_4(cau, cc, mode=Mode.SYNTHETIC)


def test_all_four_datasets_produced(synthetic_result):
    assert set(synthetic_result.coverage) == {
        "Cost and Usage", "Contract Commitment", "Billing Period", "Invoice Detail"
    }


def test_synthetic_datasets_are_flagged_and_never_fully_conformant(synthetic_result):
    m = synthetic_result.manifest
    for name in ("Billing Period", "Invoice Detail", "Contract Commitment"):
        assert m["datasets"][name]["status"] == "PRODUCED_SYNTHETIC"
        assert m["datasets"][name]["conformance"] == "SYNTHETIC"
    assert m["assumptions_present"] is True


def test_cost_and_usage_is_synthetic_only_via_invoice_backlink(synthetic_result):
    # In synthetic mode Cost and Usage is labelled synthetic solely because its
    # InvoiceDetailId back-links to the (synthetic) Invoice Detail. Every other column
    # is a faithful migration (no other assumed lineage).
    entry = synthetic_result.manifest["datasets"]["Cost and Usage"]
    assert entry["status"] == "PRODUCED_SYNTHETIC"
    assert entry["conformance"] == "SYNTHETIC"
    assumed = [c for c, p in entry["columns"].items() if p["lineage"] == "ASSUMED"]
    assert assumed == ["InvoiceDetailId"]


def test_strict_cost_and_usage_is_clean(source_tables):
    from focus_data_toolkit.convert import convert_to_focus_1_4
    from focus_data_toolkit.modes import Mode

    cau, cc = source_tables[("aws", "1.3")]
    entry = convert_to_focus_1_4(cau, cc, mode=Mode.STRICT).manifest["datasets"]["Cost and Usage"]
    assert entry["status"] == "PRODUCED"
    assert entry["conformance"] == "STRUCTURAL_LINT"


def test_assumed_values_are_marked_assumed(synthetic_result):
    id_cols = synthetic_result.manifest["datasets"]["Invoice Detail"]["columns"]
    for col in ("InvoiceIssueStatus", "PaymentTerms", "ReferenceInvoiceId", "InvoiceDetailId"):
        assert id_cols[col]["lineage"] == "ASSUMED", col


def test_assumptions_are_deterministic(source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    a = convert_to_focus_1_4(cau, cc, mode=Mode.SYNTHETIC, validate=False)
    b = convert_to_focus_1_4(cau, cc, mode=Mode.SYNTHETIC, validate=False)
    assert a.datasets == b.datasets


def test_produced_synthetic_still_passes_structural_lint(synthetic_result):
    # Synthetic data is structurally well-formed even though it is not conformant.
    for report in synthetic_result.reports.values():
        assert report.ok


@pytest.mark.parametrize("mode", ["strict", "synthetic"])
def test_mode_accepts_string(source_tables, mode):
    cau, cc = source_tables[("aws", "1.3")]
    result = convert_to_focus_1_4(cau, cc, mode=mode)
    assert result.mode == Mode(mode)


def _with_contract_applied_casing(rows, legacy):
    """Copy ``rows``, rewriting ContractApplied identifier keys to one casing."""
    pairs = [('"ContractID"', '"ContractId"'), ('"ContractCommitmentID"', '"ContractCommitmentId"')]
    if legacy:
        pairs = [(new, old) for old, new in pairs]
    out = []
    for row in rows:
        row = dict(row)
        value = row.get("ContractApplied") or ""
        for src, dst in pairs:
            value = value.replace(src, dst)
        row["ContractApplied"] = value
        out.append(row)
    return out


def test_legacy_contract_applied_casing_is_normalized_with_diagnostic(source_tables):
    # Accepting the pre-erratum ContractID casing is a normalization, not silent
    # equivalence: the conversion must surface FDT-CA-001 (and emit canonical 1.4 keys).
    cau, cc = source_tables[("aws", "1.3")]
    legacy = _with_contract_applied_casing(cau, legacy=True)
    result = convert_to_focus_1_4(legacy, cc, mode=Mode.SYNTHETIC, validate=False)
    codes = [d.code for d in result.diagnostics]
    assert codes.count("FDT-CA-001") == 1
    converted = [r["ContractApplied"] for r in result.datasets["Cost and Usage"]
                 if r.get("ContractApplied")]
    assert converted and all("ContractID" not in v for v in converted)
    assert any(d["rule_id"] == "FDT-CA-001" for d in result.manifest["diagnostics"])


def test_canonical_contract_applied_casing_converts_without_diagnostic(source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    canonical = _with_contract_applied_casing(cau, legacy=False)
    result = convert_to_focus_1_4(canonical, cc, mode=Mode.SYNTHETIC, validate=False)
    assert all(d.code != "FDT-CA-001" for d in result.diagnostics)
