"""Load supplement files into indexed, join-ready tables.

Kind resolution: an explicit ``:kind`` wins; otherwise the header is matched against the
kind registry (all join keys present + at least one fact column). Ambiguity or no match
is a hard, explained error — never a guess. CSV inputs go through :class:`CsvRowReader`
(gzip auto-detected); JSON inputs are a list of flat objects.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from focus_data_toolkit.errors import Diagnostic, Severity
from focus_data_toolkit.io.csv_io import CsvRowReader
from focus_data_toolkit.supplement.kinds import SUPPLEMENT_KINDS, SupplementKind
from focus_data_toolkit.supplement.spec import SupplementError, SupplementFileSpec

JoinKey = tuple[str, ...]

_SAMPLE_CAP = 25


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_rows(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SupplementError(f"{path}: invalid JSON: {exc}") from exc
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise SupplementError(f"{path}: JSON supplement must be a list of objects")
        rows = [{str(k): "" if v is None else str(v) for k, v in r.items()} for r in data]
        header: dict[str, None] = {}
        for row in rows:
            for key in row:
                header.setdefault(key)
        return tuple(header), rows
    reader = CsvRowReader(path)
    try:
        return reader.source_columns, [dict(record.values) for record in reader]
    finally:
        reader.close()


def detect_kind(header: Sequence[str]) -> SupplementKind:
    """Match a header against the kind registry; ambiguity / no match raise."""
    present = set(header)
    candidates = [
        kind
        for kind in SUPPLEMENT_KINDS.values()
        if set(kind.join_keys) <= present and (present & kind.columns)
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SupplementError(
            "supplement header matches no known kind (need all join keys plus at least "
            f"one fact column); known kinds: {', '.join(sorted(SUPPLEMENT_KINDS))}"
        )
    names = ", ".join(sorted(k.name for k in candidates))
    raise SupplementError(
        f"supplement header is ambiguous between kinds: {names}; force one with FILE:KIND"
    )


@dataclass
class SupplementTable:
    """One loaded supplement file, indexed on its kind's join key."""

    kind: SupplementKind
    path: Path
    sha256: str
    row_count: int
    fact_columns: tuple[str, ...]  # kind fact columns actually present in the header
    unknown_columns: tuple[str, ...]  # non-join, non-fact, non-x_ header columns
    provenance: str | None
    as_of: str | None
    rows: dict[JoinKey, dict[str, str]] = field(default_factory=dict)
    duplicate_keys: tuple[JoinKey, ...] = ()

    def lookup(self, key: JoinKey) -> Mapping[str, str] | None:
        return self.rows.get(key)

    def value(self, key: JoinKey, column: str) -> str:
        row = self.rows.get(key)
        return (row.get(column) or "").strip() if row else ""

    def manifest_entry(self) -> dict:
        entry: dict = {
            "path": self.path.name,
            "kind": self.kind.name,
            "sha256": self.sha256,
            "row_count": self.row_count,
            "columns": sorted(self.fact_columns),
        }
        if self.provenance:
            entry["provenance"] = self.provenance
        if self.as_of:
            entry["as_of"] = self.as_of
        return entry


def _load_table(spec: SupplementFileSpec) -> SupplementTable:
    path = spec.path
    if not path.is_file():
        raise SupplementError(f"supplement file not found: {path}")
    header, raw_rows = _read_rows(path)
    if spec.kind is not None:
        kind = SUPPLEMENT_KINDS.get(spec.kind)
        if kind is None:
            raise SupplementError(
                f"{path}: unknown supplement kind {spec.kind!r}; known: "
                + ", ".join(sorted(SUPPLEMENT_KINDS))
            )
        missing = [k for k in kind.join_keys if k not in header]
        if missing:
            raise SupplementError(
                f"{path}: kind {kind.name!r} requires join key column(s) "
                + ", ".join(missing)
            )
    else:
        kind = detect_kind(header)

    fact_columns = tuple(sorted(set(header) & kind.columns))
    unknown = tuple(
        sorted(
            c
            for c in header
            if c not in kind.columns and c not in kind.join_keys and not c.startswith("x_")
        )
    )
    rows: dict[JoinKey, dict[str, str]] = {}
    duplicates: list[JoinKey] = []
    for raw in raw_rows:
        key = tuple((raw.get(k) or "").strip() for k in kind.join_keys)
        if key in rows:
            duplicates.append(key)
            continue
        rows[key] = {c: (raw.get(c) or "").strip() for c in fact_columns}
    return SupplementTable(
        kind=kind,
        path=path,
        sha256=_sha256(path),
        row_count=len(raw_rows),
        fact_columns=fact_columns,
        unknown_columns=unknown,
        provenance=spec.provenance,
        as_of=spec.as_of,
        rows=rows,
        duplicate_keys=tuple(duplicates),
    )


@dataclass
class SupplementBundle:
    """All loaded supplement tables, one per kind."""

    tables: dict[str, SupplementTable] = field(default_factory=dict)

    @classmethod
    def load(cls, specs: Sequence[SupplementFileSpec]) -> SupplementBundle:
        tables: dict[str, SupplementTable] = {}
        for spec in specs:
            table = _load_table(spec)
            if table.kind.name in tables:
                raise SupplementError(
                    f"multiple supplement files of kind {table.kind.name!r} "
                    f"({tables[table.kind.name].path.name}, {table.path.name}); "
                    "merge them into one file per kind"
                )
            tables[table.kind.name] = table
        return cls(tables=tables)

    def get(self, kind_name: str) -> SupplementTable | None:
        return self.tables.get(kind_name)

    def __bool__(self) -> bool:
        return bool(self.tables)

    def manifest_entries(self) -> list[dict]:
        return [self.tables[name].manifest_entry() for name in sorted(self.tables)]

    def structural_diagnostics(self) -> list[Diagnostic]:
        """Per-table structural findings (duplicates, unknown columns)."""
        out: list[Diagnostic] = []
        for name in sorted(self.tables):
            table = self.tables[name]
            if table.duplicate_keys:
                sample = ["|".join(k) for k in table.duplicate_keys[:_SAMPLE_CAP]]
                out.append(
                    Diagnostic(
                        code="FDT-SUPP-001",
                        severity=Severity.ERROR,
                        message=f"duplicate join key(s) in supplement {table.path.name}",
                        datasets=(table.kind.target_dataset,),
                        file=str(table.path),
                        context={
                            "kind": name,
                            "duplicate_count": str(len(table.duplicate_keys)),
                            "sample": "; ".join(sample),
                        },
                    )
                )
            if table.unknown_columns:
                out.append(
                    Diagnostic(
                        code="FDT-SUPP-003",
                        severity=Severity.ERROR,
                        message=(
                            f"supplement {table.path.name} carries column(s) that are neither "
                            f"{name!r} fact columns nor x_-prefixed (typo?)"
                        ),
                        datasets=(table.kind.target_dataset,),
                        file=str(table.path),
                        context={"kind": name, "columns": ", ".join(table.unknown_columns)},
                    )
                )
        return out
