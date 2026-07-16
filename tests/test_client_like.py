"""End-to-end pipeline on a hand-authored consolidated client-like export.

This fixture is independent of the internal generators (see fixtures/client_like/SOURCES.md):
a heterogeneous FOCUS 1.3 Cost and Usage file mixing providers, issuers, currencies and
accounts. It exercises detection -> conversion -> cross-dataset validation together.
"""

from __future__ import annotations

from pathlib import Path

from focus_data_toolkit.convert import convert_to_focus_1_4, read_csv_rows
from focus_data_toolkit.modes import Mode
from focus_data_toolkit.schema.detection import detect_focus_schema
from focus_data_toolkit.validate import validate_dataset_bundle

FIXTURE = Path(__file__).parent / "fixtures" / "client_like" / "consolidated_multi_provider_1_3.csv"


def test_consolidated_export_detected_as_1_3():
    rows = read_csv_rows(FIXTURE)
    detection = detect_focus_schema(rows[0].keys())
    assert detection.dataset == "Cost and Usage"
    assert detection.detected_version == "1.3"
    assert detection.confidence == "HIGH"


def test_consolidated_export_converts_and_lints_clean():
    rows = read_csv_rows(FIXTURE)
    result = convert_to_focus_1_4(rows, mode=Mode.SYNTHETIC)
    assert result.reports["Cost and Usage"].ok
    assert result.contexts["multi_provider"]
    assert result.contexts["multi_issuer"]
    assert result.contexts["multi_currency"]


def test_consolidated_export_keeps_issuers_and_grains_separate():
    rows = read_csv_rows(FIXTURE)
    result = convert_to_focus_1_4(rows, mode=Mode.SYNTHETIC)
    details = result.datasets["Invoice Detail"]
    # AWS Usage, AWS Tax, Microsoft (EUR), Reseller X -> 4 distinct business-grain lines.
    assert len(details) == 4
    assert {d["InvoiceIssuerName"] for d in details} == {"AWS", "Microsoft", "Reseller X"}
    # Local ids are unmistakably toolkit-generated, never presented as issuer-assigned.
    assert all(d["InvoiceDetailId"].startswith("x_fdt_idl_v1_") for d in details)


def test_consolidated_bundle_validates_clean():
    rows = read_csv_rows(FIXTURE)
    result = convert_to_focus_1_4(rows, mode=Mode.SYNTHETIC)
    report = validate_dataset_bundle(dict(result.datasets))
    assert report.ok, [d.message for d in report.errors]
