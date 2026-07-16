"""Parquet reader/writer with exact decimal columnar I/O (P1.6).

PyArrow is an **optional** dependency (``pip install 'focus-data-toolkit[parquet]'``); importing
this module without it raises a clear, actionable error rather than an opaque ``ImportError``.

Type mapping (driven by the FOCUS 1.4 model + the committed decimal-scale registry):

* ``Decimal`` → :func:`pyarrow.decimal128` at the column's ``(precision, scale)`` — **never**
  a binary float, so financial values keep exact decimal semantics. A value with more
  fractional digits than the column scale raises (line-numbered) instead of rounding silently.
* ``Date/Time`` → ``timestamp('us', tz=UTC)``.
* ``JSON`` / ``String`` → ``string`` (the JSON text is preserved verbatim).
* Nulls: an empty string ``""`` maps to a null (validity bit 0) and reads back as ``""``.

Exactness contract: CSV output is exact **at the literal** (byte-for-byte); Parquet output is
exact **in decimal value** — ``decimal128`` normalises the representation (``35.2`` is stored
and re-read as ``35.200000000000``), so equivalence tests compare Parquet by ``Decimal`` value,
not by string. Reading is bounded-memory (batched row groups); writing flushes row groups so
memory does not scale with row count.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import Path

from focus_data_toolkit.io.records import DatasetSchema, MalformedRecordError, Record
from focus_data_toolkit.model import column_spec, load_model, resolve_dataset

_PARQUET_HINT = (
    "Parquet support requires PyArrow. Install it with: pip install 'focus-data-toolkit[parquet]'"
)


def _require_pyarrow():
    """Import PyArrow or raise a clear install hint (kept out of the core import graph)."""
    try:
        import pyarrow as pa  # noqa: PLC0415
        import pyarrow.parquet as pq  # noqa: PLC0415
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised via monkeypatch
        raise MalformedRecordError(_PARQUET_HINT) from exc
    return pa, pq


# Rows per row group when writing / batch when reading (bounds memory).
_BATCH = 10_000
# Timestamps are stored at microsecond precision (covers FOCUS millisecond timestamps exactly).
_DECIMAL_SCALE_FILE = "focus_1_4_decimal_scale.json"


@cache
def _decimal_scale_registry() -> dict:
    path = Path(__file__).resolve().parent.parent / "model" / _DECIMAL_SCALE_FILE
    return json.loads(path.read_text(encoding="utf-8"))


def decimal_precision_scale(column: str) -> tuple[int, int]:
    """Return the ``(precision, scale)`` for a Decimal column (registry override or default)."""
    reg = _decimal_scale_registry()
    spec = reg["columns"].get(column, reg["default"])
    return int(spec["precision"]), int(spec["scale"])


def arrow_schema(dataset: str, columns: Sequence[str]):
    """Build the PyArrow schema for ``columns`` of ``dataset`` from the FOCUS model types."""
    pa, _ = _require_pyarrow()
    dataset = resolve_dataset(dataset)
    fields = []
    for col in columns:
        data_type = column_spec(dataset, col)["data_type"]
        if data_type == "Decimal":
            precision, scale = decimal_precision_scale(col)
            arrow_type = pa.decimal128(precision, scale)
        elif data_type == "Date/Time":
            arrow_type = pa.timestamp("us", tz="UTC")
        else:  # String, JSON -> UTF-8 string (JSON text preserved verbatim)
            arrow_type = pa.string()
        fields.append(pa.field(col, arrow_type, nullable=True))
    return pa.schema(fields)


def _parse_timestamp(value: str, column: str, line: int) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise MalformedRecordError(
            f"column {column!r}: {value!r} is not an ISO-8601 datetime", line_number=line
        ) from exc
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def _to_decimal(value: str, column: str, line: int) -> Decimal | None:
    text = value.strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise MalformedRecordError(
            f"column {column!r}: {value!r} is not a decimal", line_number=line
        ) from exc


def _column_arrays(pa, dataset: str, columns: Sequence[str], rows: list[Mapping[str, str]], base_line: int):
    """Convert a batch of string rows into typed Arrow arrays (one per column)."""
    arrays = []
    for col in columns:
        data_type = column_spec(dataset, col)["data_type"]
        if data_type == "Decimal":
            precision, scale = decimal_precision_scale(col)
            arrow_type = pa.decimal128(precision, scale)
            values = [_to_decimal(r.get(col, ""), col, base_line + i) for i, r in enumerate(rows)]
            try:
                arrays.append(pa.array(values, type=arrow_type))
            except pa.lib.ArrowInvalid:
                # Pinpoint the offending row: a value needing more scale than the column allows
                # must fail loudly with its line, never round silently.
                for i, v in enumerate(values):
                    if v is None:
                        continue
                    try:
                        pa.array([v], type=arrow_type)
                    except pa.lib.ArrowInvalid as exc:
                        raise MalformedRecordError(
                            f"column {col!r}: {v} exceeds decimal128({precision},{scale}) scale",
                            line_number=base_line + i,
                        ) from exc
                raise
        elif data_type == "Date/Time":
            values = [_parse_timestamp(r.get(col, ""), col, base_line + i) for i, r in enumerate(rows)]
            arrays.append(pa.array(values, type=pa.timestamp("us", tz="UTC")))
        else:
            values = [(r.get(col, "") or None) for r in rows]
            arrays.append(pa.array(values, type=pa.string()))
    return arrays


def _stringify(dataset: str, column: str, value) -> str:
    """Render a typed Arrow value back to the toolkit's canonical string form."""
    if value is None:
        return ""
    data_type = column_spec(dataset, column)["data_type"]
    if data_type == "Date/Time":
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        dt = dt.astimezone(UTC)
        text = dt.isoformat()
        return text[:-6] + "Z" if text.endswith("+00:00") else text
    return str(value)


def dataset_metadata(dataset: str, *, version: str, mode: str, conformance: str, tool_version: str) -> dict:
    """Business (deterministic) file metadata; operational keys live in a separate namespace."""
    return {
        "focus.dataset": resolve_dataset(dataset),
        "focus.target_version": version,
        "focus.mode": mode,
        "focus.conformance": conformance,
        "focus.toolkit_version": tool_version,
    }


class ParquetRowWriter:
    """Write rows to a Parquet file in ``schema.columns`` order, flushing bounded row groups."""

    def __init__(self, path: str | Path, schema: DatasetSchema, *, metadata: Mapping[str, str] | None = None) -> None:
        self._pa, self._pq = _require_pyarrow()
        self._dataset = resolve_dataset(schema.dataset)
        self._columns = tuple(schema.columns)
        arrow = arrow_schema(self._dataset, self._columns)
        if metadata:
            arrow = arrow.with_metadata({k: str(v) for k, v in metadata.items()})
        self._arrow_schema = arrow
        self._writer = self._pq.ParquetWriter(str(path), arrow)
        self._buffer: list[Mapping[str, str]] = []
        self._written = 0

    def write(self, values: Mapping[str, str]) -> None:
        self._buffer.append(values)
        if len(self._buffer) >= _BATCH:
            self._flush()

    def _flush(self) -> None:
        if not self._buffer:
            return
        arrays = _column_arrays(
            self._pa, self._dataset, self._columns, self._buffer, base_line=self._written + 1
        )
        batch = self._pa.record_batch(arrays, schema=self._arrow_schema)
        self._writer.write_table(self._pa.Table.from_batches([batch], self._arrow_schema))
        self._written += len(self._buffer)
        self._buffer = []

    def close(self) -> None:
        self._flush()
        self._writer.close()

    def __enter__(self) -> ParquetRowWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class ParquetRowReader:
    """Stream ``Record``s from a Parquet file, rendering typed values back to strings."""

    def __init__(self, path: str | Path, *, dataset: str | None = None) -> None:
        self._pa, self._pq = _require_pyarrow()
        self._path = Path(path)
        self._file = self._pq.ParquetFile(str(path))
        self.source_columns: tuple[str, ...] = tuple(self._file.schema_arrow.names)
        self._dataset = self._resolve_dataset(dataset)

    def _resolve_dataset(self, dataset: str | None) -> str:
        if dataset is not None:
            return resolve_dataset(dataset)
        # Infer the dataset from the column set so values can be rendered by their model types.
        cols = set(self.source_columns)
        best, best_overlap = "Cost and Usage", -1
        for name in load_model()["datasets"]:
            overlap = len(cols & set(load_model()["datasets"][name]["columns"]))
            if overlap > best_overlap:
                best, best_overlap = name, overlap
        return best

    def __iter__(self) -> Iterator[Record]:
        model_cols = set(load_model()["datasets"][self._dataset]["columns"])
        line = 0
        for batch in self._file.iter_batches(batch_size=_BATCH):
            columns = batch.schema.names
            pydata = {name: batch.column(i).to_pylist() for i, name in enumerate(columns)}
            for r in range(batch.num_rows):
                line += 1
                values = {}
                for name in columns:
                    raw = pydata[name][r]
                    values[name] = (
                        _stringify(self._dataset, name, raw) if name in model_cols
                        else ("" if raw is None else str(raw))
                    )
                yield Record(values, line)

    def close(self) -> None:
        pass  # ParquetFile has no long-lived handle to close in this usage

    def __enter__(self) -> ParquetRowReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def open_parquet_writer(path: str | Path, schema: DatasetSchema, *, metadata: Mapping[str, str] | None = None):
    """Open ``path`` for Parquet writing and return ``(writer, writer)`` (handle == writer).

    The tuple shape mirrors :func:`focus_data_toolkit.io.csv_io.open_csv_writer` so callers can
    treat both formats uniformly; the Parquet writer owns its own file handle.
    """
    writer = ParquetRowWriter(path, schema, metadata=metadata)
    return writer, writer


__all__ = [
    "ParquetRowReader",
    "ParquetRowWriter",
    "arrow_schema",
    "dataset_metadata",
    "decimal_precision_scale",
    "open_parquet_writer",
]
