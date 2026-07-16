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
