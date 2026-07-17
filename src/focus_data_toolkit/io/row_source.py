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

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, runtime_checkable

from focus_data_toolkit.io.csv_io import CsvRowReader
from focus_data_toolkit.io.records import Record

_PARQUET_MAGIC = b"PAR1"


@runtime_checkable
class RowSource(Protocol):
    """A streaming, closeable source of rows with a known header."""

    source_columns: tuple[str, ...]

    def __iter__(self) -> Iterator[Record]: ...

    def close(self) -> None: ...


def is_parquet(path: str | Path) -> bool:
    """Whether ``path`` is a Parquet file (magic bytes; ``.parquet`` suffix as fallback)."""
    p = Path(path)
    try:
        with open(p, "rb") as fh:
            return fh.read(4) == _PARQUET_MAGIC
    except OSError:
        return p.suffix.lower() == ".parquet"


def open_row_source(path: str | Path, *, dataset: str | None = None) -> RowSource:
    """Open ``path`` as a :class:`RowSource`, auto-detecting CSV (``.gz`` ok) vs Parquet.

    ``dataset`` optionally names the FOCUS dataset the file holds, letting the Parquet
    reader render typed values by the right model columns instead of inferring the dataset
    from the header overlap. CSV input ignores it (values are already strings).
    """
    if is_parquet(path):
        from focus_data_toolkit.io.parquet_io import ParquetRowReader

        return ParquetRowReader(path, dataset=dataset)
    return CsvRowReader(path)


def read_source_rows(path: str | Path, *, dataset: str | None = None) -> list[dict[str, str]]:
    """Materialise a CSV/Parquet source into a list of dict rows (eager-path input)."""
    reader = open_row_source(path, dataset=dataset)
    try:
        return [dict(record.values) for record in reader]
    finally:
        reader.close()


__all__ = ["RowSource", "is_parquet", "open_row_source", "read_source_rows"]
