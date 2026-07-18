"""Format-agnostic row input: open a CSV or Parquet source behind one protocol.

Client exports arrive as CSV (possibly gzipped) or Parquet. :func:`open_row_source` detects
the physical format — by the ``PAR1`` magic bytes first, extension as a fallback for
unreadable paths — and returns the matching streaming reader. Both readers already share
the same shape (``source_columns``, iteration yielding ``Record``, ``close``); the
:class:`RowSource` protocol names that contract so conversion code can consume either
without caring which it got.

Parquet support requires the ``[parquet]`` extra; opening a Parquet file without it raises
the same actionable :class:`~focus_data_toolkit.io.records.MalformedRecordError` install
hint as Parquet output does.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol, runtime_checkable

from focus_data_toolkit.io.csv_io import CsvRowReader
from focus_data_toolkit.io.records import MalformedRecordError, Record

_PARQUET_MAGIC = b"PAR1"


@runtime_checkable
class RowSource(Protocol):
    """A streaming, closeable source of rows with a known header."""

    source_columns: tuple[str, ...]

    def __iter__(self) -> Iterator[Record]: ...

    def close(self) -> None: ...


def is_parquet(path: str | os.PathLike[str]) -> bool:
    """Whether ``path`` is a Parquet file (magic bytes; ``.parquet`` suffix as fallback)."""
    p = Path(path)
    try:
        with open(p, "rb") as fh:
            return fh.read(4) == _PARQUET_MAGIC
    except OSError:
        return p.suffix.lower() == ".parquet"


def _hive_partition_columns(base_dir: Path) -> tuple[str, ...]:
    """Partition column names of a Hive-layout directory, walking ``COL=value`` segments."""
    cols: list[str] = []
    current = base_dir
    while True:
        subdirs = [e for e in current.iterdir() if e.is_dir() and "=" in e.name]
        if not subdirs:
            return tuple(cols)
        cols.append(subdirs[0].name.split("=", 1)[0])
        current = subdirs[0]


def open_row_source(path: str | os.PathLike[str], *, dataset: str | None = None) -> RowSource:
    """Open ``path`` as a :class:`RowSource`, auto-detecting the physical format.

    A file is sniffed as CSV (``.gz`` ok) or Parquet; a directory is read as a
    Hive-partitioned Parquet dataset (partition columns inferred from the ``COL=value``
    path segments). ``dataset`` optionally names the FOCUS dataset the file holds, letting
    the Parquet readers render typed values by the right model columns instead of inferring
    the dataset from the header overlap. CSV input ignores it (values are already strings).
    """
    p = Path(path)
    if p.is_dir():
        from focus_data_toolkit.io.parquet_io import PartitionedParquetReader

        with _parquet_errors(p):
            return PartitionedParquetReader(
                p, dataset or "Cost and Usage", _hive_partition_columns(p)
            )
    if is_parquet(p):
        from focus_data_toolkit.io.parquet_io import ParquetRowReader

        with _parquet_errors(p):
            return ParquetRowReader(p, dataset=dataset)
    return CsvRowReader(p)


@contextmanager
def _parquet_errors(path: Path) -> Iterator[None]:
    """Translate Arrow open/read failures into the CLI-friendly MalformedRecordError.

    A missing-pyarrow MalformedRecordError (with its install hint) passes through untouched.
    """
    try:
        yield
    except MalformedRecordError:
        raise
    except (OSError, ValueError) as exc:
        # pyarrow's ArrowInvalid/ArrowIOError derive from ValueError/OSError, so a corrupt
        # or truncated Parquet file surfaces as a clean input error, never a traceback.
        raise MalformedRecordError(f"cannot read Parquet source {path}: {exc}") from exc


def read_source_rows(
    path: str | os.PathLike[str], *, dataset: str | None = None
) -> list[dict[str, str]]:
    """Materialise a CSV/Parquet source into a list of dict rows (eager-path input)."""
    reader = open_row_source(path, dataset=dataset)
    try:
        if isinstance(reader, CsvRowReader):
            return [dict(record.values) for record in reader]
        with _parquet_errors(Path(path)):  # iteration can hit mid-file corruption too
            return [dict(record.values) for record in reader]
    finally:
        reader.close()


__all__ = ["RowSource", "is_parquet", "open_row_source", "read_source_rows"]
