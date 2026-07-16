"""Conversion manifest: structure, provenance, and determinism."""

from __future__ import annotations

from focus_data_toolkit.convert import convert_to_focus_1_4
from focus_data_toolkit.manifest import render
from focus_data_toolkit.modes import Mode


def _convert(source_tables, mode):
    cau, cc = source_tables[("aws", "1.3")]
    return convert_to_focus_1_4(cau, cc, mode=mode)


def test_manifest_top_level_shape(source_tables):
    m = _convert(source_tables, Mode.STRICT).manifest
    assert m["target_version"] == "1.4"
    assert m["source_version"] == "1.3"
    assert m["mode"] == "strict"
    assert set(m["datasets"]) == {
        "Cost and Usage", "Contract Commitment", "Billing Period", "Invoice Detail"
    }


def test_every_column_has_provenance(source_tables):
    m = _convert(source_tables, Mode.SYNTHETIC).manifest
    for name, entry in m["datasets"].items():
        assert entry["columns"], name
        for col, prov in entry["columns"].items():
            assert prov["lineage"] in {
                "OBSERVED", "RENAMED", "DERIVED", "ENRICHED", "ASSUMED", "UNAVAILABLE"
            }, (name, col)


def test_not_produced_entries_have_reason_and_blockers(source_tables):
    m = _convert(source_tables, Mode.STRICT).manifest
    for name in ("Billing Period", "Invoice Detail", "Contract Commitment"):
        entry = m["datasets"][name]
        assert entry["status"] == "NOT_PRODUCED"
        assert entry["conformance"] == "INCOMPLETE"
        assert entry["reason"]
        assert entry["blocking_columns"]


def test_assumptions_present_tracks_mode(source_tables):
    assert _convert(source_tables, Mode.STRICT).manifest["assumptions_present"] is False
    assert _convert(source_tables, Mode.SYNTHETIC).manifest["assumptions_present"] is True


def test_strict_produced_datasets_have_no_assumed_columns(source_tables):
    result = _convert(source_tables, Mode.STRICT)
    for name in result.coverage:
        lineages = {c["lineage"] for c in result.manifest["datasets"][name]["columns"].values()}
        assert "ASSUMED" not in lineages, name


def test_manifest_is_deterministic(source_tables):
    a = _convert(source_tables, Mode.SYNTHETIC).manifest
    b = _convert(source_tables, Mode.SYNTHETIC).manifest
    assert render(a) == render(b)
    # render is sorted/stable
    assert render(a).endswith("}\n")


# --- conformance is set after the lint runs, not before (review C7) ---------- #
def test_conformance_not_validated_without_lint(source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    m = convert_to_focus_1_4(cau, cc, validate=False).manifest
    assert m["datasets"]["Cost and Usage"]["conformance"] == "NOT_VALIDATED"


def test_conformance_structural_lint_after_passing_lint(source_tables):
    m = _convert(source_tables, Mode.STRICT).manifest
    assert m["datasets"]["Cost and Usage"]["conformance"] == "STRUCTURAL_LINT"


def test_conformance_lint_failed_on_bad_source(source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    bad = [dict(r) for r in cau]
    bad[0]["BillingCurrency"] = "ZZ"  # not an ISO 4217 code
    result = convert_to_focus_1_4(bad, cc)
    assert not result.reports["Cost and Usage"].ok
    assert result.manifest["datasets"]["Cost and Usage"]["conformance"] == "LINT_FAILED"
    assert result.ok is False


# --- empty derived datasets are NOT_PRODUCED, not headerless files (review C8) ---- #
def test_synthetic_invoice_detail_not_produced_when_no_invoice_id(source_tables):
    cau, _ = source_tables[("gcp", "1.2")]
    stripped = [dict(r, InvoiceId="") for r in cau]
    result = convert_to_focus_1_4(stripped, mode=Mode.SYNTHETIC)
    assert "Invoice Detail" not in result.datasets
    entry = result.manifest["datasets"]["Invoice Detail"]
    assert entry["status"] == "NOT_PRODUCED"
    assert "no derivable rows" in entry["reason"]


def test_empty_contract_commitment_source_not_produced(source_tables):
    cau, _ = source_tables[("aws", "1.3")]
    result = convert_to_focus_1_4(cau, [], mode=Mode.SYNTHETIC)
    assert "Contract Commitment" not in result.datasets
    assert result.manifest["datasets"]["Contract Commitment"]["status"] == "NOT_PRODUCED"


# --- backfilled pricing columns are derived, not observed (review C9) ---------- #
def test_pricing_currency_columns_are_derived(source_tables):
    prov = _convert(source_tables, Mode.STRICT).provenance["Cost and Usage"]
    assert str(prov["PricingCurrency"].lineage) == "DERIVED"
    assert str(prov["PricingCurrencyEffectiveCost"].lineage) == "DERIVED"
