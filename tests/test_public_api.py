"""The package-root API: everything a library caller needs, importable from one place.

Per docs/versioning.md only names re-exported at the package root are versioned public
API. A library caller converting client data with supplements must therefore be able to
build the bundle, pick the mode, inspect gaps and handle every documented failure without
importing an internal module.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

import focus_data_toolkit as fdt
from focus_data_toolkit import convert, io, modes, supplement


def test_every_public_name_resolves_once():
    assert len(fdt.__all__) == len(set(fdt.__all__))
    for name in fdt.__all__:
        assert hasattr(fdt, name), name


@pytest.mark.parametrize(
    ("name", "origin"),
    [
        ("Mode", modes.Mode),
        ("ConversionError", convert.ConversionError),
        ("AtomicWriteError", io.atomic_writer.AtomicWriteError),
        ("SupplementBundle", supplement.SupplementBundle),
        ("SupplementFileSpec", supplement.SupplementFileSpec),
        ("SupplementError", supplement.SupplementError),
        ("GapReport", supplement.GapReport),
        ("compute_gaps", supplement.compute_gaps),
        ("load_bundle_dir", supplement.load_bundle_dir),
    ],
)
def test_root_reexports_are_the_defining_objects(name, origin):
    # A re-export, never a copy: isinstance checks and except clauses must keep working.
    assert getattr(fdt, name) is origin


def _write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_strict_conversion_with_supplements_through_the_root_api_only(tmp_path, source_tables):
    cau, _ = source_tables[("aws", "1.2")]
    periods = sorted({
        (r["InvoiceIssuerName"].strip(), r["BillingPeriodStart"].strip(),
         r["BillingPeriodEnd"].strip())
        for r in cau
    })
    rows = [
        {"InvoiceIssuerName": i, "BillingPeriodStart": s, "BillingPeriodEnd": e,
         "BillingPeriodCreated": s, "BillingPeriodLastUpdated": e,
         "BillingPeriodStatus": "Closed"}
        for i, s, e in periods
    ]
    spec = fdt.SupplementFileSpec(path=_write_csv(tmp_path / "bp.csv", rows))
    bundle = fdt.SupplementBundle.load([spec])

    gaps = fdt.compute_gaps(cau[0].keys(), "1.2")
    assert isinstance(gaps, fdt.GapReport)
    assert gaps.blocking("Billing Period"), "without supplements Billing Period is blocked"

    result = fdt.convert_to_focus_1_4(cau, mode=fdt.Mode.STRICT, supplements=bundle)
    assert result.manifest["datasets"]["Billing Period"]["status"] == "PRODUCED"
    # Invoice Detail received no supplement: strict mode still refuses to invent it.
    assert result.manifest["datasets"]["Invoice Detail"]["status"] == "NOT_PRODUCED"


def test_documented_failures_are_catchable_from_the_root(tmp_path):
    with pytest.raises(fdt.ConversionError):
        fdt.convert_to_focus_1_4([])
    with pytest.raises(fdt.SupplementError):
        fdt.load_bundle_dir(tmp_path)
