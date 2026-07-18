"""Streaming CSV reader/writer.

``CsvRowReader`` reads a CSV (transparently gzip-decompressing ``.gz`` inputs) one row at a
time, owning the physical line number for actionable errors, and rejecting malformed rows
(wrong field count) rather than silently mangling them. ``CsvRowWriter`` writes rows in the
schema's column order using the same ``csv`` dialect as the eager ``rows_to_csv_bytes`` path,
so streaming output is byte-identical to the in-memory reference.
"""

from __future__ import annotations

import csv
import gzip
import io
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TextIO

from focus_data_toolkit.io.records import DatasetSchema, MalformedRecordError, Record

_GZIP_MAGIC = b"\x1f\x8b"


def _open_text(path: Path, encoding: str) -> TextIO:
    with open(path, "rb") as probe:
        magic = probe.read(2)
    if magic == _GZIP_MAGIC:
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding=encoding, newline="")
    return open(path, newline="", encoding=encoding)


class CsvRowReader:
    """Stream ``Record``s from a CSV file (``.gz`` auto-detected)."""

    def __init__(self, path: str | Path, *, encoding: str = "utf-8") -> None:
        self._path = Path(path)
        self._fh = _open_text(self._path, encoding)
        self._reader = csv.reader(self._fh)
        try:
            header = next(self._reader)
        except StopIteration:
            header = []
        self.source_columns: tuple[str, ...] = tuple(header)

    def __iter__(self) -> Iterator[Record]:
        ncols = len(self.source_columns)
        for row in self._reader:
            if len(row) != ncols:
                raise MalformedRecordError(
                    f"malformed CSV record at line {self._reader.line_num}: expected {ncols} "
                    f"field(s), got {len(row)}",
                    line_number=self._reader.line_num,
                )
            yield Record(dict(zip(self.source_columns, row, strict=True)), self._reader.line_num)

    @property
    def bytes_total(self) -> int | None:
        """Size of the source file in bytes (the *compressed* size for a ``.gz`` input)."""
        try:
            return self._path.stat().st_size
        except OSError:
            return None

    @property
    def bytes_read(self) -> int | None:
        """Approximate position in the *compressed* byte stream, for progress reporting.

        Both this and :attr:`bytes_total` are measured on the compressed stream, so the
        ratio is consistent for gzip input. The value advances in buffered read-ahead steps
        (monotonic, slightly ahead of the last yielded row) — fine for a progress bar. For a
        gzip source the compressed offset lives on the wrapped raw file object, not on the
        ``GzipFile`` (whose ``tell()`` is the *uncompressed* offset); returns ``None`` if the
        underlying stream exposes no usable position.
        """
        try:
            buffer = self._fh.buffer  # the TextIOWrapper's underlying binary stream
        except (AttributeError, ValueError):
            return None
        # gzip: the compressed offset is on the wrapped raw file, exposed as ``fileobj``.
        raw = getattr(buffer, "fileobj", None)
        target = raw if raw is not None else buffer
        try:
            pos = target.tell()
        except (OSError, ValueError, AttributeError):
            return None
        return pos if isinstance(pos, int) and pos >= 0 else None

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> CsvRowReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class CsvRowWriter:
    """Write rows to a text stream in ``schema.columns`` order.

    The header is written lazily on the first row, so a zero-row dataset produces an empty
    file (matching ``rows_to_csv_bytes([])``). The default ``csv`` dialect (CRLF line
    terminator) matches the eager writer exactly.
    """

    def __init__(self, stream: TextIO, schema: DatasetSchema) -> None:
        self._schema = schema
        self._writer = csv.DictWriter(stream, fieldnames=list(schema.columns), extrasaction="ignore")
        self._header_written = False

    def write(self, values: Mapping[str, str]) -> None:
        if not self._header_written:
            self._writer.writeheader()
            self._header_written = True
        self._writer.writerow({col: values.get(col, "") for col in self._schema.columns})

    def close(self) -> None:
        pass


def open_csv_writer(path: str | Path, schema: DatasetSchema, *, encoding: str = "utf-8"):
    """Open ``path`` for writing and return ``(file_handle, CsvRowWriter)``.

    The file is opened with ``newline=""`` so the ``csv`` module owns line endings (no OS
    translation), matching the in-memory ``io.StringIO`` reference byte-for-byte.
    """
    handle = open(path, "w", newline="", encoding=encoding)
    return handle, CsvRowWriter(handle, schema)
