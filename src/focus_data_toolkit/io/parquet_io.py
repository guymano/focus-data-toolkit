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
from decimal import Decimal
from functools import cache
from pathlib import Path
from urllib.parse import quote

from focus_data_toolkit.io.records import DatasetSchema, MalformedRecordError, Record
from focus_data_toolkit.model import column_spec, load_model, resolve_dataset

# Reuse the linter's FOCUS format predicates so Parquet coercion refuses exactly what the CSV
# lint gate refuses. Otherwise Decimal()/fromisoformat() would accept non-FOCUS literals (`+1`,
# `.5`, `1E+7`, an offset/naive datetime), normalise them to decimal128/UTC, and the read-back
# lint would never see the original defect.
from focus_data_toolkit.model.validator import _decimal_or_none, _is_utc_datetime

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

# Parquet compression codecs the CLI/API accept ("none" -> uncompressed).
COMPRESSIONS = ("snappy", "zstd", "gzip", "none")
# Partition columns must be low-cardinality identifiers, not measures/JSON. String and Date/Time
# both round-trip losslessly through Hive directory names (reconstructed as strings on read).
_PARTITIONABLE_TYPES = frozenset({"String", "Date/Time"})
# Empty partition values are written as an empty segment (``COL=``), which reads back as "".
# The reader disables Hive's null convention with a sentinel that percent-encoding can never
# produce, so a *real* value equal to Hive's ``__HIVE_DEFAULT_PARTITION__`` token is not
# silently turned into null. (Percent-encoded values only ever contain [A-Za-z0-9_.~%-].)
_NO_HIVE_NULL = "\x00__fdt_never_null__"
# High-cardinality guards: warn past the soft threshold, refuse past the hard cap (each distinct
# partition holds an open writer, so an unbounded key would exhaust file handles / memory).
PARTITION_WARN_THRESHOLD = 100
MAX_PARTITIONS = 1000


def partitionable_columns(dataset: str, columns: Sequence[str]) -> list[str]:
    """Return the requested partition columns that are NOT valid partition keys (for an error).

    A valid key exists in ``dataset`` and is String or Date/Time typed; Decimal/JSON measures and
    unknown columns are rejected so partitioning stays on low-cardinality identifiers.
    """
    dataset = resolve_dataset(dataset)
    model_cols = load_model()["datasets"][dataset]["columns"]
    bad = []
    for col in columns:
        spec = model_cols.get(col)
        if spec is None or spec.get("data_type") not in _PARTITIONABLE_TYPES:
            bad.append(col)
    return bad


def _hive_segment(column: str, value: str) -> str:
    """Render one ``column=value`` Hive path segment (percent-encoded; empty -> ``COL=``).

    An empty value becomes an empty segment rather than Hive's ``__HIVE_DEFAULT_PARTITION__``
    sentinel, so a real value that happens to equal that sentinel can never collide with it.
    """
    return f"{column}={quote(value, safe='')}"


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
    # Enforce the FOCUS Date/Time format (ISO-8601 UTC with a 'Z') *before* normalising, so an
    # offset (`+00:00`) or naive value is refused here exactly as the CSV lint gate refuses it.
    if not _is_utc_datetime(text):
        raise MalformedRecordError(
            f"column {column!r}: {value!r} is not a FOCUS Date/Time (ISO-8601 UTC, ...Z)",
            line_number=line,
        )
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def _to_decimal(value: str, column: str, line: int) -> Decimal | None:
    text = value.strip()
    if not text:
        return None
    # Enforce the FOCUS NumericFormat before coercion: `+1`, `.5`, `1E+7`, NaN/Inf are parseable
    # by Decimal but violate the spec and would pass a read-back lint once normalised.
    parsed = _decimal_or_none(text)
    if parsed is None:
        raise MalformedRecordError(
            f"column {column!r}: {value!r} is not a FOCUS numeric literal", line_number=line
        )
    return parsed


def _column_arrays(pa, dataset: str, columns: Sequence[str], rows: list[Mapping[str, str]], base_line: int):
    """Convert a batch of string rows into typed Arrow arrays (one per column)."""
    arrays = []
    for col in columns:
        data_type = column_spec(dataset, col)["data_type"]
        if data_type == "Decimal":
            precision, scale = decimal_precision_scale(col)
            arrow_type = pa.decimal128(precision, scale)
            # `values` is reused by the Date/Time and String branches below; a broad element
            # type keeps mypy happy across the mutually-exclusive branches (Arrow validates it).
            values: list = [_to_decimal(r.get(col, ""), col, base_line + i) for i, r in enumerate(rows)]
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
    if isinstance(value, str):
        # Already textual (e.g. a client export storing every column as strings): pass it
        # through untouched — downstream validation judges the format, not the reader.
        return value
    data_type = column_spec(dataset, column)["data_type"]
    if data_type == "Date/Time":
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        dt = dt.astimezone(UTC)
        text = dt.isoformat()
        return text[:-6] + "Z" if text.endswith("+00:00") else text
    return str(value)


def dataset_metadata(
    dataset: str,
    *,
    target_version: str,
    source_version: str,
    mode: str,
    conformance: str,
    tool_version: str,
) -> dict:
    """Business (deterministic) file metadata; operational keys live in a separate namespace.

    ``target_version`` is the FOCUS version the file conforms to (1.4); ``source_version`` is the
    version it was converted from. Keeping them distinct stops a Parquet-metadata reader from
    mistaking the source version for the file's own FOCUS version.
    """
    return {
        "focus.dataset": resolve_dataset(dataset),
        "focus.target_version": target_version,
        "focus.source_version": source_version,
        "focus.mode": mode,
        "focus.conformance": conformance,
        "focus.toolkit_version": tool_version,
    }


class ParquetRowWriter:
    """Write rows to a Parquet file in ``schema.columns`` order, flushing bounded row groups."""

    def __init__(
        self,
        path: str | Path,
        schema: DatasetSchema,
        *,
        metadata: Mapping[str, str] | None = None,
        compression: str = "snappy",
    ) -> None:
        self._pa, self._pq = _require_pyarrow()
        self._dataset = resolve_dataset(schema.dataset)
        self._columns = tuple(schema.columns)
        arrow = arrow_schema(self._dataset, self._columns)
        if metadata:
            arrow = arrow.with_metadata({k: str(v) for k, v in metadata.items()})
        self._arrow_schema = arrow
        self._writer = self._pq.ParquetWriter(str(path), arrow, compression=compression)
        self._buffer: list[Mapping[str, str]] = []
        self._written = 0

    @property
    def buffered(self) -> int:
        """Rows currently buffered (not yet flushed to a row group)."""
        return len(self._buffer)

    def write(self, values: Mapping[str, str]) -> None:
        self._buffer.append(values)
        if len(self._buffer) >= _BATCH:
            self._flush()

    def flush(self) -> None:
        """Flush buffered rows to a row group (public; used to bound memory across writers)."""
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


class PartitionTooWideError(MalformedRecordError):
    """Raised when a ``--partition-by`` key produces more partitions than :data:`MAX_PARTITIONS`."""


class PartitionedParquetWriter:
    """Write a dataset as a Hive-partitioned Parquet tree under ``base_dir``.

    Rows are routed to ``base_dir/COL=value/.../part-N.parquet`` by their partition-column values;
    the partition columns are **omitted** from the part files (standard Hive — a reader
    reconstructs them from the path). Open file handles scale with the *number of partitions*
    (hence the hard :data:`MAX_PARTITIONS` cap), but **buffered rows are bounded globally**: once
    the total unflushed rows across all partitions reaches :data:`_BATCH`, every partition writer
    is flushed — so memory stays bounded no matter how the rows interleave across partition keys.
    When ``target_file_size`` is set, a partition rolls to a new part file once its (approximate,
    uncompressed) running size crosses the threshold.
    """

    def __init__(
        self,
        base_dir: str | Path,
        schema: DatasetSchema,
        partition_by: Sequence[str],
        *,
        metadata: Mapping[str, str] | None = None,
        compression: str = "snappy",
        target_file_size: int | None = None,
    ) -> None:
        if target_file_size is not None and target_file_size <= 0:
            raise ValueError(f"target_file_size must be positive, got {target_file_size}")
        self._base = Path(base_dir)
        self._dataset = resolve_dataset(schema.dataset)
        self._partition_by = tuple(partition_by)
        # Part files carry every column except the partition columns (Hive stores those in path).
        self._file_columns = tuple(c for c in schema.columns if c not in set(self._partition_by))
        self._metadata = metadata
        self._compression = compression
        self._target = target_file_size
        # Per partition: (writer, part_index, running_byte_estimate).
        self._writers: dict[tuple[str, ...], list] = {}
        self._buffered = 0  # total rows buffered across all partition writers

    def _partition_dir(self, values: Mapping[str, str]) -> tuple[tuple[str, ...], Path]:
        key = tuple((values.get(c) or "") for c in self._partition_by)
        rel = Path(*[_hive_segment(c, v) for c, v in zip(self._partition_by, key, strict=True)])
        return key, self._base / rel

    def _open_part(self, directory: Path, index: int):
        directory.mkdir(parents=True, exist_ok=True)
        return ParquetRowWriter(
            directory / f"part-{index}.parquet",
            DatasetSchema(self._dataset, self._file_columns),
            metadata=self._metadata,
            compression=self._compression,
        )

    def write(self, values: Mapping[str, str]) -> None:
        key, directory = self._partition_dir(values)
        state = self._writers.get(key)
        if state is None:
            if len(self._writers) >= MAX_PARTITIONS:
                raise PartitionTooWideError(
                    f"--partition-by produced more than {MAX_PARTITIONS} partitions; choose a "
                    "lower-cardinality key"
                )
            state = [self._open_part(directory, 0), 0, 0]
            self._writers[key] = state
        if self._target is not None and state[2] >= self._target:
            state[0].close()
            state[1] += 1
            state[2] = 0
            state[0] = self._open_part(directory, state[1])
        state[0].write(values)
        self._buffered += 1
        if self._target is not None:
            state[2] += sum(len(values.get(c) or "") for c in self._file_columns) + len(
                self._file_columns
            )
        # Bound total buffered rows across ALL partitions (not per-partition): flush everything
        # once the global buffer fills, so memory can't grow with the number of partition keys.
        if self._buffered >= _BATCH:
            for st in self._writers.values():
                st[0].flush()
            self._buffered = 0

    def partition_count(self) -> int:
        return len(self._writers)

    def close(self) -> None:
        for state in self._writers.values():
            state[0].close()


class PartitionedParquetReader:
    """Read a Hive-partitioned Parquet dataset back as ``Record``s (partition columns rebuilt).

    Used by the lint gate: the partition columns (omitted from the part files) are reconstructed
    from the directory names via an explicit **string** Hive schema, so a Date/Time or numeric-
    looking partition value is never re-typed and reads back exactly as written.
    """

    def __init__(self, base_dir: str | Path, dataset: str, partition_by: Sequence[str]) -> None:
        self._pa, _ = _require_pyarrow()
        import pyarrow.dataset as pds  # noqa: PLC0415

        self._dataset = resolve_dataset(dataset)
        self._partition_cols = set(partition_by)
        schema = self._pa.schema([(c, self._pa.string()) for c in partition_by])
        partitioning = pds.HivePartitioning(
            schema, null_fallback=_NO_HIVE_NULL, segment_encoding="uri"
        )
        self._ds = pds.dataset(str(base_dir), format="parquet", partitioning=partitioning)
        self.source_columns: tuple[str, ...] = tuple(self._ds.schema.names)

    def __iter__(self) -> Iterator[Record]:
        model_cols = set(load_model()["datasets"][self._dataset]["columns"])
        line = 0
        for batch in self._ds.to_batches(batch_size=_BATCH):
            columns = batch.schema.names
            pydata = {name: batch.column(i).to_pylist() for i, name in enumerate(columns)}
            for r in range(batch.num_rows):
                line += 1
                values = {}
                for name in columns:
                    raw = pydata[name][r]
                    if name in self._partition_cols:
                        # Reconstructed from the path as a string already — pass it through.
                        values[name] = "" if raw is None else str(raw)
                    elif name in model_cols:
                        values[name] = _stringify(self._dataset, name, raw)
                    else:
                        values[name] = "" if raw is None else str(raw)
                yield Record(values, line)

    def close(self) -> None:
        pass


def open_parquet_writer(
    path: str | Path,
    schema: DatasetSchema,
    *,
    metadata: Mapping[str, str] | None = None,
    compression: str = "snappy",
):
    """Open ``path`` for Parquet writing and return ``(writer, writer)`` (handle == writer).

    The tuple shape mirrors :func:`focus_data_toolkit.io.csv_io.open_csv_writer` so callers can
    treat both formats uniformly; the Parquet writer owns its own file handle.
    """
    writer = ParquetRowWriter(path, schema, metadata=metadata, compression=compression)
    return writer, writer


__all__ = [
    "COMPRESSIONS",
    "MAX_PARTITIONS",
    "PartitionedParquetReader",
    "PartitionedParquetWriter",
    "PartitionTooWideError",
    "ParquetRowReader",
    "ParquetRowWriter",
    "arrow_schema",
    "dataset_metadata",
    "decimal_precision_scale",
    "open_parquet_writer",
    "partitionable_columns",
]
