"""Streaming record abstraction shared by the CSV and Parquet I/O layers.

A :class:`Record` is one source row plus its physical line/record number (for actionable
errors). :class:`RowReader` / :class:`RowWriter` are the minimal streaming interfaces the
conversion engine consumes and produces; :class:`DatasetSchema` supplies the output column
order from the model registry (never from ``rows[0]``, which a stream cannot peek).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class MalformedRecordError(ValueError):
    """A source record could not be parsed (e.g. wrong field count); carries the line number."""

    def __init__(self, message: str, *, line_number: int | None = None) -> None:
        super().__init__(message)
        self.line_number = line_number


@dataclass(frozen=True)
class Record:
    """One source row and its 1-based physical line/record number."""

    values: Mapping[str, str]
    line_number: int


@dataclass(frozen=True)
class DatasetSchema:
    """Output schema of a FOCUS dataset: the column names, in normative model order."""

    dataset: str
    columns: tuple[str, ...]


@runtime_checkable
class RowReader(Protocol):
    """A stream of :class:`Record`s whose header (``source_columns``) is known on open."""

    source_columns: tuple[str, ...]

    def __iter__(self) -> Iterator[Record]: ...

    def close(self) -> None: ...


@runtime_checkable
class RowWriter(Protocol):
    """A sink for output rows (values keyed by column name)."""

    def write(self, values: Mapping[str, str]) -> None: ...

    def close(self) -> None: ...


class ListRowReader:
    """Adapt an in-memory ``list[dict]`` to the :class:`RowReader` interface.

    Lets the small-volume ``convert_to_focus_1_4(rows)`` API run the exact same streaming
    engine as ``convert_files`` (equivalence by construction, not by luck).
    """

    def __init__(self, rows: Iterable[Mapping[str, str]], *, source_columns: tuple[str, ...] | None = None) -> None:
        self._rows = list(rows)
        if source_columns is not None:
            self.source_columns = source_columns
        else:
            self.source_columns = tuple(self._rows[0].keys()) if self._rows else ()

    def __iter__(self) -> Iterator[Record]:
        for i, row in enumerate(self._rows, start=1):
            yield Record(row, i)

    def close(self) -> None:
        pass


class ListRowWriter:
    """Collect written rows into an in-memory list (the streaming counterpart of a sink)."""

    def __init__(self) -> None:
        self.rows: list[dict[str, str]] = []

    def write(self, values: Mapping[str, str]) -> None:
        self.rows.append(dict(values))

    def close(self) -> None:
        pass
