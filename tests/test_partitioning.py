"""Parquet partitioning (P1.6): Hive-partitioned output, round-trip, guards, CLI wiring."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from focus_data_toolkit.cli import main
from focus_data_toolkit.convert import ConversionError, convert_files, read_csv_rows
from focus_data_toolkit.generators import get_generator
from focus_data_toolkit.io.records import DatasetSchema

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from focus_data_toolkit.io.parquet_io import (  # noqa: E402
    PartitionedParquetReader,
    PartitionedParquetWriter,
    PartitionTooWideError,
    _hive_segment,
    partitionable_columns,
)

_CU = "Cost and Usage"


def _source(tmp_path: Path, n: int = 200) -> Path:
    cau = tmp_path / "cau.csv"
    cau.write_bytes(get_generator("aws", "1.3").generate_csv_bytes(n, 1302))
    return cau


# --- unit-level -----------------------------------------------------------------------------


def test_partitionable_columns_rejects_non_string_datetime():
    # BilledCost is Decimal, Tags is JSON, Nope is unknown -> all rejected; issuer/currency ok.
    bad = partitionable_columns(_CU, ["InvoiceIssuerName", "BilledCost", "Tags", "Nope"])
    assert bad == ["BilledCost", "Tags", "Nope"]
    assert partitionable_columns(_CU, ["BillingCurrency", "BillingPeriodStart"]) == []


def test_hive_segment_encodes_specials_and_empty():
    assert _hive_segment("BillingPeriodStart", "2026-01-01T00:00:00Z") == (
        "BillingPeriodStart=2026-01-01T00%3A00%3A00Z"
    )
    assert _hive_segment("BillingCurrency", "") == "BillingCurrency=__HIVE_DEFAULT_PARTITION__"


def test_writer_refuses_too_many_partitions(tmp_path, monkeypatch):
    import focus_data_toolkit.io.parquet_io as pqio

    monkeypatch.setattr(pqio, "MAX_PARTITIONS", 2)
    w = PartitionedParquetWriter(tmp_path / "cu", DatasetSchema(_CU, ("BilledCost", "BillingCurrency")), ["BillingCurrency"])
    w.write({"BilledCost": "1", "BillingCurrency": "USD"})
    w.write({"BilledCost": "1", "BillingCurrency": "EUR"})
    with pytest.raises(PartitionTooWideError):
        w.write({"BilledCost": "1", "BillingCurrency": "GBP"})


def test_partitioned_round_trip_reconstructs_string_and_datetime_partitions(tmp_path):
    cols = ("BilledCost", "BillingCurrency", "InvoiceIssuerName", "BillingPeriodStart")
    part_by = ["BillingCurrency", "BillingPeriodStart"]
    rows = [
        {"BilledCost": "35.2", "BillingCurrency": "USD", "InvoiceIssuerName": "AWS", "BillingPeriodStart": "2026-01-01T00:00:00Z"},
        {"BilledCost": "10", "BillingCurrency": "EUR", "InvoiceIssuerName": "Azure", "BillingPeriodStart": "2026-01-01T00:00:00Z"},
        {"BilledCost": "-5.5", "BillingCurrency": "USD", "InvoiceIssuerName": "AWS", "BillingPeriodStart": "2026-02-01T00:00:00Z"},
    ]
    base = tmp_path / "cu"
    w = PartitionedParquetWriter(base, DatasetSchema(_CU, cols), part_by)
    for r in rows:
        w.write(r)
    w.close()
    out = [rec.values for rec in PartitionedParquetReader(base, _CU, part_by)]
    assert len(out) == 3
    assert sum(Decimal(o["BilledCost"]) for o in out) == Decimal("39.7")
    # Partition values reconstructed exactly (datetime keeps its 'Z', currency its string).
    assert {o["BillingPeriodStart"] for o in out} == {"2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"}
    assert {o["BillingCurrency"] for o in out} == {"USD", "EUR"}
    # Non-partition column preserved inside the part files.
    assert {o["InvoiceIssuerName"] for o in out} == {"AWS", "Azure"}


# --- convert_files end-to-end ----------------------------------------------------------------


def test_convert_files_partitioned_layout_and_reconciliation(tmp_path):
    cau = _source(tmp_path, 300)
    out = convert_files(
        str(cau), str(tmp_path / "pq"), mode="synthetic", output_format="parquet",
        partition_by=["BillingCurrency"],
    )
    cu_dir = out / "synthetic_focus_1_4_cost_and_usage"
    assert cu_dir.is_dir()  # partitioned dataset is a directory
    assert all(p.name.startswith("BillingCurrency=") for p in cu_dir.iterdir())
    # Other datasets remain single files.
    assert (out / "synthetic_focus_1_4_invoice_detail.parquet").is_file()

    read = list(PartitionedParquetReader(cu_dir, _CU, ["BillingCurrency"]))
    src = read_csv_rows(cau)
    assert len(read) == len(src) == 300
    assert sum(Decimal(r.values["BilledCost"]) for r in read) == sum(
        Decimal(r["BilledCost"] or "0") for r in src
    )


def test_partitioned_manifest_and_checksums(tmp_path):
    cau = _source(tmp_path, 120)
    out = convert_files(
        str(cau), str(tmp_path / "pq"), mode="synthetic", output_format="parquet",
        partition_by=["BillingCurrency", "ChargeCategory"],
    )
    manifest = json.loads((out / "focus_1_4_manifest.json").read_text())
    cu = manifest["datasets"]["Cost and Usage"]
    assert cu["output_file"] == "synthetic_focus_1_4_cost_and_usage"  # a directory, no .parquet
    assert cu["partitioned_by"] == ["BillingCurrency", "ChargeCategory"]
    # SHA256SUMS enrolls each part file under its relative path (not just a basename).
    sums = (out / "SHA256SUMS").read_text().splitlines()
    part_lines = [ln for ln in sums if "synthetic_focus_1_4_cost_and_usage/" in ln]
    assert part_lines and all("/part-" in ln for ln in part_lines)
    assert "_index.sqlite" not in os.listdir(out)


def test_target_file_size_rolls_part_files(tmp_path):
    cau = _source(tmp_path, 300)
    out = convert_files(
        str(cau), str(tmp_path / "pq"), mode="synthetic", output_format="parquet",
        partition_by=["BillingCurrency"], target_file_size=2000,
    )
    cu_dir = out / "synthetic_focus_1_4_cost_and_usage"
    parts = list(cu_dir.rglob("*.parquet"))
    partitions = list(cu_dir.iterdir())
    assert len(parts) > len(partitions)  # at least one partition split into multiple parts
    # All rows still present and reconciled.
    read = list(PartitionedParquetReader(cu_dir, _CU, ["BillingCurrency"]))
    assert len(read) == 300


@pytest.mark.parametrize("compression", ["snappy", "zstd", "gzip", "none"])
def test_compression_codecs_are_accepted(tmp_path, compression):
    cau = _source(tmp_path, 40)
    out = convert_files(
        str(cau), str(tmp_path / f"pq_{compression}"), mode="synthetic",
        output_format="parquet", compression=compression,
    )
    assert (out / "synthetic_focus_1_4_cost_and_usage.parquet").is_file()


def test_bad_compression_is_rejected(tmp_path):
    cau = _source(tmp_path, 10)
    with pytest.raises(ConversionError, match="unsupported compression"):
        convert_files(str(cau), str(tmp_path / "o"), mode="synthetic",
                      output_format="parquet", compression="lz4x")


def test_bad_partition_column_is_rejected(tmp_path):
    cau = _source(tmp_path, 10)
    with pytest.raises(ConversionError, match="cannot partition on"):
        convert_files(str(cau), str(tmp_path / "o"), mode="synthetic",
                      output_format="parquet", partition_by=["BilledCost"])


def test_partition_by_requires_parquet(tmp_path):
    cau = _source(tmp_path, 10)
    with pytest.raises(ConversionError, match="requires --output-format parquet"):
        convert_files(str(cau), str(tmp_path / "o"), mode="synthetic", partition_by=["BillingCurrency"])


def test_high_cardinality_partition_warns(tmp_path, monkeypatch):
    import focus_data_toolkit.io.parquet_io as pqio

    monkeypatch.setattr(pqio, "PARTITION_WARN_THRESHOLD", 1)  # force the soft warning
    cau = _source(tmp_path, 120)
    out = convert_files(
        str(cau), str(tmp_path / "pq"), mode="synthetic", output_format="parquet",
        partition_by=["BillingCurrency", "ChargeCategory"],
    )
    manifest = json.loads((out / "focus_1_4_manifest.json").read_text())
    codes = {d["rule_id"] for d in manifest.get("diagnostics", [])}
    assert "FDT-IO-004" in codes


# --- CLI --------------------------------------------------------------------------------------


def test_cli_partitioned_parquet(tmp_path, capsys):
    cau = _source(tmp_path, 60)
    rc = main(["convert", "--cost-and-usage", str(cau), "--out", str(tmp_path / "o"),
               "--mode", "synthetic", "--output-format", "parquet",
               "--partition-by", "BillingCurrency", "--compression", "zstd"])
    assert rc == 4
    cu_dir = tmp_path / "o" / "synthetic_focus_1_4_cost_and_usage"
    assert cu_dir.is_dir() and any(cu_dir.rglob("*.parquet"))


def test_cli_partition_by_without_parquet_exits_2(tmp_path, capsys):
    cau = _source(tmp_path, 20)
    rc = main(["convert", "--cost-and-usage", str(cau), "--out", str(tmp_path / "o"),
               "--mode", "synthetic", "--partition-by", "BillingCurrency"])
    assert rc == 2
    assert "requires --output-format parquet" in capsys.readouterr().err
