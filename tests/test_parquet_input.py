"""Parquet as *input*: sources are format-sniffed and converge with the CSV path (PR-10)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from focus_data_toolkit.cli import main
from focus_data_toolkit.convert import convert_files
from focus_data_toolkit.io.row_source import is_parquet, open_row_source, read_source_rows
from focus_data_toolkit.modes import Mode

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")


def write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_parquet(path: Path, rows: list[dict[str, str]]) -> Path:
    """Client-like Parquet: every column stored as strings (a common naive export)."""
    cols = list(rows[0].keys())
    pq.write_table(pa.table({c: [r[c] for r in rows] for c in cols}), path)
    return path


@pytest.fixture
def source(source_tables):
    cau, _ = source_tables[("aws", "1.2")]
    return cau


@pytest.fixture
def cc_tables(source_tables):
    _, cc = source_tables[("aws", "1.3")]
    return cc


def test_is_parquet_detection(tmp_path, source):
    parquet = write_parquet(tmp_path / "data.bin", source)  # magic wins over extension
    assert is_parquet(parquet)
    assert not is_parquet(write_csv(tmp_path / "data.csv", source))
    assert is_parquet(tmp_path / "missing.parquet")  # unreadable -> extension fallback
    assert not is_parquet(tmp_path / "missing.csv")


def test_open_row_source_round_trips_string_parquet(tmp_path, source):
    reader = open_row_source(write_parquet(tmp_path / "cau.parquet", source))
    assert reader.source_columns == tuple(source[0].keys())
    assert read_source_rows(tmp_path / "cau.parquet") == source


def test_streaming_parquet_source_equals_csv_source(tmp_path, source, cc_tables):
    csv_out = convert_files(
        write_csv(tmp_path / "cau.csv", source), tmp_path / "from-csv",
        contract_commitment=write_csv(tmp_path / "cc.csv", cc_tables),
        mode=Mode.SYNTHETIC,
    )
    parquet_out = convert_files(
        write_parquet(tmp_path / "cau.parquet", source), tmp_path / "from-parquet",
        contract_commitment=write_parquet(tmp_path / "cc.parquet", cc_tables),
        mode=Mode.SYNTHETIC,
    )
    csv_files = {p.name for p in csv_out.iterdir()}
    assert csv_files == {p.name for p in parquet_out.iterdir()}
    for name in sorted(csv_files - {"_run.json"}):  # sidecar carries per-run ids
        assert (csv_out / name).read_bytes() == (parquet_out / name).read_bytes(), name


def test_streaming_parquet_source_with_supplements(tmp_path, source):
    # The supplement key-collection pre-pass must read the Parquet source too.
    from test_supplement_apply import _billing_period_rows

    from focus_data_toolkit.supplement import SupplementBundle, SupplementFileSpec

    bundle = SupplementBundle.load([
        SupplementFileSpec(path=write_csv(tmp_path / "bp.csv", _billing_period_rows(source)))
    ])
    out = convert_files(
        write_parquet(tmp_path / "cau.parquet", source), tmp_path / "out",
        mode=Mode.STRICT, supplements=bundle,
    )
    manifest = json.loads((out / "focus_1_4_manifest.json").read_text(encoding="utf-8"))
    assert manifest["datasets"]["Billing Period"]["status"] == "PRODUCED"


def test_eager_cli_accepts_parquet_source(tmp_path, source):
    src = write_parquet(tmp_path / "cau.parquet", source)
    out = tmp_path / "out"
    rc = main(["convert", "--cost-and-usage", str(src), "--out", str(out),
               "--mode", "synthetic"])
    assert rc == 4  # synthetic mode with assumptions present
    assert (out / "focus_1_4_manifest.json").exists()


def test_gaps_cli_reads_parquet_header(tmp_path, source):
    src = write_parquet(tmp_path / "cau.parquet", source)
    out = tmp_path / "gaps.json"
    rc = main(["gaps", "--cost-and-usage", str(src), "--format", "json", "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["source_version"] == "1.2"
