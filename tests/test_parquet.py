"""Parquet I/O (P1.6): exact decimal128 round-trip, nulls, dates, JSON, metadata, CLI wiring."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from focus_data_toolkit.convert import ConversionError, convert_files, read_csv_rows
from focus_data_toolkit.generators import get_generator
from focus_data_toolkit.io.parquet_io import (
    ParquetRowReader,
    ParquetRowWriter,
    arrow_schema,
    decimal_precision_scale,
)
from focus_data_toolkit.io.records import DatasetSchema, MalformedRecordError

# pyarrow is an optional extra; skip the whole module (and the parquet code paths it drives) if
# it is not installed, rather than failing the core test run.
pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

_CU = "Cost and Usage"


def _write_read(tmp_path: Path, columns, rows, *, metadata=None):
    path = tmp_path / "d.parquet"
    with ParquetRowWriter(path, DatasetSchema(_CU, tuple(columns)), metadata=metadata) as w:
        for r in rows:
            w.write(r)
    return path, [rec.values for rec in ParquetRowReader(path, dataset=_CU)]


def test_decimal_columns_are_value_exact(tmp_path):
    cols = ("BilledCost", "ContractedUnitPrice")
    rows = [
        {"BilledCost": "35.2", "ContractedUnitPrice": "0.0000000116"},
        {"BilledCost": "-12.500000", "ContractedUnitPrice": "1.5"},
        {"BilledCost": "0", "ContractedUnitPrice": "999999999999.123456"},
    ]
    _, out = _write_read(tmp_path, cols, rows)
    # Parquet is exact in *decimal value*, not string literal: compare with Decimal.
    for src, got in zip(rows, out, strict=True):
        assert Decimal(got["BilledCost"]) == Decimal(src["BilledCost"])
        assert Decimal(got["ContractedUnitPrice"]) == Decimal(src["ContractedUnitPrice"])


def test_decimals_stored_as_decimal128_never_float(tmp_path):
    path, _ = _write_read(tmp_path, ("BilledCost",), [{"BilledCost": "35.2"}])
    field = pq.ParquetFile(str(path)).schema_arrow.field("BilledCost")
    assert pa.types.is_decimal(field.type)
    assert not pa.types.is_floating(field.type)


def test_scale_overflow_raises_with_line_number(tmp_path):
    # 13 fractional digits exceeds the default decimal128 scale (12) -> must raise, not round.
    path = tmp_path / "of.parquet"
    with pytest.raises(MalformedRecordError) as excinfo:
        with ParquetRowWriter(path, DatasetSchema(_CU, ("BilledCost",))) as w:
            w.write({"BilledCost": "1.0"})
            w.write({"BilledCost": "0.0000000000005"})
    assert excinfo.value.line_number == 2


def test_empty_string_is_null_and_reads_back_empty(tmp_path):
    cols = ("BilledCost", "BillingPeriodStart", "Tags", "BillingCurrency")
    rows = [{"BilledCost": "", "BillingPeriodStart": "", "Tags": "", "BillingCurrency": ""}]
    path, out = _write_read(tmp_path, cols, rows)
    assert out[0] == {c: "" for c in cols}
    # Genuinely stored as null (validity bit 0), not the literal "".
    table = pq.read_table(str(path))
    assert table.column("BillingCurrency").null_count == 1


def test_datetime_roundtrips_as_utc(tmp_path):
    cols = ("BillingPeriodStart",)
    rows = [{"BillingPeriodStart": "2024-01-01T00:00:00Z"}, {"BillingPeriodStart": "2024-03-15T12:30:00.500Z"}]
    path, out = _write_read(tmp_path, cols, rows)
    assert pa.types.is_timestamp(pq.ParquetFile(str(path)).schema_arrow.field("BillingPeriodStart").type)
    assert out[0]["BillingPeriodStart"] == "2024-01-01T00:00:00Z"
    assert out[1]["BillingPeriodStart"] == "2024-03-15T12:30:00.500000Z"


def test_json_text_is_preserved_verbatim(tmp_path):
    cols = ("Tags", "BillingCurrency")
    payload = '{"env":"prod","x_team":"data"}'
    _, out = _write_read(tmp_path, cols, [{"Tags": payload, "BillingCurrency": "USD"}])
    assert out[0]["Tags"] == payload
    assert json.loads(out[0]["Tags"]) == {"env": "prod", "x_team": "data"}


def test_file_metadata_is_written(tmp_path):
    meta = {"focus.dataset": "Cost and Usage", "focus.target_version": "1.4", "focus.mode": "synthetic"}
    path, _ = _write_read(tmp_path, ("BillingCurrency",), [{"BillingCurrency": "USD"}], metadata=meta)
    stored = pq.ParquetFile(str(path)).schema_arrow.metadata
    assert stored[b"focus.dataset"] == b"Cost and Usage"
    assert stored[b"focus.target_version"] == b"1.4"


def test_unit_price_columns_use_higher_scale():
    assert decimal_precision_scale("BilledCost") == (38, 12)
    assert decimal_precision_scale("ContractedUnitPrice") == (38, 16)


def test_arrow_schema_types_follow_the_model():
    schema = arrow_schema(_CU, ("BilledCost", "BillingPeriodStart", "Tags", "BillingCurrency"))
    assert pa.types.is_decimal(schema.field("BilledCost").type)
    assert pa.types.is_timestamp(schema.field("BillingPeriodStart").type)
    assert pa.types.is_string(schema.field("Tags").type)
    assert pa.types.is_string(schema.field("BillingCurrency").type)


def test_convert_files_parquet_reconciles_with_csv(tmp_path):
    module = get_generator("aws", "1.3")
    cau = tmp_path / "cau.csv"
    cau.write_bytes(module.generate_csv_bytes(300, 1302))

    out = convert_files(str(cau), str(tmp_path / "pq"), mode="synthetic", output_format="parquet")
    files = sorted(os.listdir(out))
    assert "synthetic_focus_1_4_cost_and_usage.parquet" in files
    assert "_index.sqlite" not in files  # scratch DB never published
    assert (out / "SHA256SUMS").read_text().count("\n") == 4  # manifest + 3 datasets

    # Invoice Detail Parquet reconciles (by decimal value) with the Cost and Usage source.
    detail = ParquetRowReader(out / "synthetic_focus_1_4_invoice_detail.parquet", dataset="Invoice Detail")
    total_inv = sum((Decimal(rec.values["BilledCost"]) for rec in detail), Decimal(0))
    cau_rows = read_csv_rows(cau)
    total_cu = sum(
        (Decimal(r["BilledCost"] or "0") for r in cau_rows if (r.get("InvoiceId") or "").strip()),
        Decimal(0),
    )
    assert abs(total_inv - total_cu) < Decimal("0.01")

    manifest = json.loads((out / "focus_1_4_manifest.json").read_text())
    assert manifest["datasets"]["Cost and Usage"]["output_file"].endswith(".parquet")


def test_convert_files_parquet_row_count_matches_csv(tmp_path):
    module = get_generator("azure", "1.3")
    cau = tmp_path / "cau.csv"
    cau.write_bytes(module.generate_csv_bytes(150, 1302))

    csv_out = convert_files(str(cau), str(tmp_path / "csv"), mode="synthetic")
    pq_out = convert_files(str(cau), str(tmp_path / "pq"), mode="synthetic", output_format="parquet")

    csv_rows = read_csv_rows(csv_out / "synthetic_focus_1_4_cost_and_usage.csv")
    pq_rows = list(ParquetRowReader(pq_out / "synthetic_focus_1_4_cost_and_usage.parquet", dataset=_CU))
    assert len(csv_rows) == len(pq_rows) == 150
    # Same BilledCost decimal values, row for row.
    for c, p in zip(csv_rows, pq_rows, strict=True):
        assert Decimal(c["BilledCost"] or "0") == Decimal(p.values["BilledCost"] or "0")


def test_bad_output_format_is_rejected(tmp_path):
    module = get_generator("aws", "1.3")
    cau = tmp_path / "cau.csv"
    cau.write_bytes(module.generate_csv_bytes(10, 1302))
    with pytest.raises(ConversionError, match="unsupported output format"):
        convert_files(str(cau), str(tmp_path / "o"), mode="synthetic", output_format="orc")


def test_metadata_records_target_and_source_version(tmp_path):
    module = get_generator("aws", "1.3")
    cau = tmp_path / "cau.csv"
    cau.write_bytes(module.generate_csv_bytes(20, 1302))
    out = convert_files(str(cau), str(tmp_path / "pq"), mode="synthetic", output_format="parquet")
    meta = pq.ParquetFile(str(out / "synthetic_focus_1_4_cost_and_usage.parquet")).schema_arrow.metadata
    # The file conforms to FOCUS 1.4; the source it was converted from is recorded separately.
    assert meta[b"focus.target_version"] == b"1.4"
    assert meta[b"focus.source_version"] == b"1.3"


@pytest.mark.parametrize("value", ["2026-05-01T00:00:00+00:00", "2026-05-01T00:00:00"])
def test_non_focus_datetime_is_rejected(tmp_path, value):
    # An offset (+00:00) or naive datetime would normalize to ...Z and slip past the read-back
    # lint; the CSV path rejects it, so Parquet coercion must too (with the row's line number).
    path = tmp_path / "dt.parquet"
    with pytest.raises(MalformedRecordError) as excinfo:
        with ParquetRowWriter(path, DatasetSchema(_CU, ("BillingPeriodStart",))) as w:
            w.write({"BillingPeriodStart": "2026-01-01T00:00:00Z"})
            w.write({"BillingPeriodStart": value})
    assert excinfo.value.line_number == 2


@pytest.mark.parametrize("value", ["+1", ".5", "1E+7", "NaN", "Infinity"])
def test_non_focus_numeric_is_rejected(tmp_path, value):
    # Parseable by Decimal but violating FOCUS NumericFormat; must be refused before coercion.
    path = tmp_path / "num.parquet"
    with pytest.raises(MalformedRecordError) as excinfo:
        with ParquetRowWriter(path, DatasetSchema(_CU, ("BilledCost",))) as w:
            w.write({"BilledCost": "1.0"})
            w.write({"BilledCost": value})
    assert excinfo.value.line_number == 2


def test_valid_focus_datetime_and_numeric_still_pass(tmp_path):
    # Non-regression: canonical Z-datetimes and plain decimals convert cleanly.
    cols = ("BillingPeriodStart", "BilledCost")
    rows = [{"BillingPeriodStart": "2026-01-01T00:00:00Z", "BilledCost": "35.2"},
            {"BillingPeriodStart": "2026-02-01T00:00:00.500Z", "BilledCost": "-0.000001"}]
    _, out = _write_read(tmp_path, cols, rows)
    assert out[0]["BillingPeriodStart"] == "2026-01-01T00:00:00Z"
    assert Decimal(out[1]["BilledCost"]) == Decimal("-0.000001")
