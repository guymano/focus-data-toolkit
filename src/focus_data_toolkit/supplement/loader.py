"""Load supplement files into indexed, join-ready tables.

Kind resolution: an explicit ``:kind`` wins; otherwise the header is matched against the
kind registry (all join keys present + at least one fact column). Ambiguity or no match
is a hard, explained error — never a guess. CSV inputs go through :class:`CsvRowReader`
(gzip auto-detected); JSON inputs are a list of flat objects.
"""

from __future__ import annotations

import gzip
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
_GZIP_MAGIC = b"\x1f\x8b"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _flatten(obj: Mapping[str, object], prefix: str = "") -> dict[str, str]:
    """Flatten a nested JSON object to dot-pathed scalar string values.

    Provider API exports (e.g. AWS ``Entity.InvoicingEntity``) are nested; adapters address
    those with dot-paths. Lists are JSON-encoded (rarely needed for supplement facts).
    """
    out: dict[str, str] = {}
    for key, value in obj.items():
        path_key = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, f"{path_key}."))
        elif value is None:
            out[path_key] = ""
        elif isinstance(value, list):
            out[path_key] = json.dumps(value, separators=(",", ":"))
        else:
            out[path_key] = str(value)
    return out


def _is_json_path(path: Path) -> bool:
    """A .json or .json.gz supplement (the compound suffix matters — Path.suffix is only .gz)."""
    lower = path.name.lower()
    return lower.endswith(".json") or lower.endswith(".json.gz")


def _read_bytes(path: Path) -> bytes:
    """Read a file, transparently gunzipping a gzip-magic (or .gz) payload."""
    raw = path.read_bytes()
    if raw[:2] == _GZIP_MAGIC or path.name.lower().endswith(".gz"):
        return gzip.decompress(raw)
    return raw


def _unwrap_json_records(data: object, path: Path) -> list[dict]:
    """Return the list of record objects from a JSON supplement.

    Accepts a bare array, or a provider API envelope — a single object whose only
    array-valued property holds the records (e.g. AWS ``{"invoiceSummaries": [...],
    "nextToken": ...}`` or ``{"savingsPlans": [...]}``). An object with several array
    properties is ambiguous and rejected rather than guessed.
    """
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        array_keys = [k for k, v in data.items() if isinstance(v, list)]
        if len(array_keys) != 1:
            raise SupplementError(
                f"{path}: JSON object supplement must wrap exactly one array of records "
                f"(found array properties: {', '.join(array_keys) or 'none'})"
            )
        records = data[array_keys[0]]
    else:
        raise SupplementError(f"{path}: JSON supplement must be an array or an envelope object")
    if not all(isinstance(r, dict) for r in records):
        raise SupplementError(f"{path}: JSON supplement records must be objects")
    return records


def _read_rows(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    if _is_json_path(path):
        try:
            data = json.loads(_read_bytes(path).decode("utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise SupplementError(f"{path}: invalid JSON: {exc}") from exc
        rows = [_flatten(r) for r in _unwrap_json_records(data, path)]
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
    # Set when a provider-native adapter translated this file ("<adapter>@<version>").
    adapter: str | None = None
    # Per fact column -> its full ENRICHED attribution label. Populated at load; a merged
    # table (several files, one kind) attributes each column to its originating file.
    column_source: dict[str, str] = field(default_factory=dict)

    def lookup(self, key: JoinKey) -> Mapping[str, str] | None:
        return self.rows.get(key)

    def value(self, key: JoinKey, column: str) -> str:
        row = self.rows.get(key)
        return (row.get(column) or "").strip() if row else ""

    @property
    def source_tag(self) -> str:
        """The provenance tag for ENRICHED values from this table."""
        return self.adapter if self.adapter is not None else self.kind.name

    def source_for(self, column: str) -> str:
        """The ENRICHED attribution for ``column`` (its originating file/adapter)."""
        return self.column_source.get(
            column, f"supplement:{self.source_tag}:{self.path.name}"
        )

    def manifest_entry(self) -> dict:
        entry: dict = {
            "path": self.path.name,
            "kind": self.kind.name,
            "sha256": self.sha256,
            "row_count": self.row_count,
            "columns": sorted(self.fact_columns),
        }
        if self.adapter is not None:
            entry["adapter"] = self.adapter
        if self.provenance:
            entry["provenance"] = self.provenance
        if self.as_of:
            entry["as_of"] = self.as_of
        return entry


def _resolve_kind(
    spec: SupplementFileSpec,
    header: Sequence[str],
    raw_rows: list[dict[str, str]],
) -> tuple[SupplementKind, list[dict[str, str]], str | None]:
    """Resolve (kind, rows, adapter_tag) for a supplement file.

    A forced ``:kind`` may name a canonical kind or a provider adapter. Without a forced
    kind, canonical header detection wins; if nothing canonical matches, provider adapters
    are tried; only then is it an error. An adapter translates the native rows into
    canonical-kind rows and returns its ``<adapter>@<version>`` tag.
    """
    from focus_data_toolkit.supplement.adapters import get_adapter, load_adapters
    from focus_data_toolkit.supplement.adapters.registry import detect_adapter

    path = spec.path
    if spec.kind is not None:
        if spec.kind in load_adapters():
            adapter = get_adapter(spec.kind)
            return _apply_adapter(adapter, path, header, raw_rows)
        kind = SUPPLEMENT_KINDS.get(spec.kind)
        if kind is None:
            raise SupplementError(
                f"{path}: unknown supplement kind/adapter {spec.kind!r}; known kinds: "
                + ", ".join(sorted(SUPPLEMENT_KINDS))
                + "; known adapters: "
                + (", ".join(sorted(load_adapters())) or "(none)")
            )
        missing = [k for k in kind.join_keys if k not in header]
        if missing:
            raise SupplementError(
                f"{path}: kind {kind.name!r} requires join key column(s) " + ", ".join(missing)
            )
        return kind, raw_rows, None

    # Canonical detection first; a native provider export won't carry FOCUS names, so it
    # falls through to adapter detection.
    canonical = [
        kind
        for kind in SUPPLEMENT_KINDS.values()
        if set(kind.join_keys) <= set(header) and (set(header) & kind.columns)
    ]
    if len(canonical) == 1:
        return canonical[0], raw_rows, None
    if len(canonical) > 1:
        return detect_kind(header), raw_rows, None  # raises the ambiguity error
    detected = detect_adapter(header)
    if detected is not None:
        return _apply_adapter(detected, path, header, raw_rows)
    detect_kind(header)  # no canonical + no adapter -> raise the canonical "no kind" error
    raise AssertionError("unreachable")  # pragma: no cover


def _apply_adapter(adapter, path, header, raw_rows):
    from focus_data_toolkit.supplement.adapters.registry import AdapterError

    kind = SUPPLEMENT_KINDS[adapter.target_kind]
    translated = adapter.translate(raw_rows)
    missing = [k for k in kind.join_keys if not any(row.get(k) for row in translated)]
    if translated and missing:
        raise AdapterError(
            f"{path}: adapter {adapter.name!r} produced no {', '.join(missing)} value "
            "(the native export is missing the field(s) that build the join key)"
        )
    return kind, translated, adapter.source_tag


def _load_table(spec: SupplementFileSpec) -> SupplementTable:
    path = spec.path
    if not path.is_file():
        raise SupplementError(f"supplement file not found: {path}")
    header, raw_rows = _read_rows(path)
    kind, rows_source, adapter_tag = _resolve_kind(spec, header, raw_rows)
    # After an adapter, the effective header is the translated FOCUS columns.
    header = tuple(dict.fromkeys(c for row in rows_source for c in row)) if adapter_tag else header
    raw_rows = rows_source

    fact_columns = tuple(sorted(set(header) & kind.columns))
    if not fact_columns:
        raise SupplementError(
            f"{path}: supplement of kind {kind.name!r} carries none of its fact columns "
            "(join keys only) — it would apply no facts. Add at least one fact column."
        )
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
    source_label = f"supplement:{adapter_tag or kind.name}:{path.name}"
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
        adapter=adapter_tag,
        column_source={c: source_label for c in fact_columns},
    )


def _merge_tables(a: SupplementTable, b: SupplementTable) -> SupplementTable:
    """Merge two supplement files of the same kind into one join-indexed table.

    Disjoint fact columns combine (the headline use: a provider-native adapter export plus
    a hand-authored file supplying the facts the adapter leaves out). The same fact column
    supplied for the same key by both files must agree; a genuine value conflict is a hard,
    explained error rather than a silent last-writer-wins.
    """
    fact_columns = tuple(sorted(set(a.fact_columns) | set(b.fact_columns)))
    rows: dict[JoinKey, dict[str, str]] = {k: dict(v) for k, v in a.rows.items()}
    for key, facts in b.rows.items():
        dst = rows.setdefault(key, {})
        for col, val in facts.items():
            existing = dst.get(col, "")
            if existing and val and existing != val:
                raise SupplementError(
                    f"conflicting supplement value for {col} at join key "
                    f"{'|'.join(key)}: {a.path.name} has {existing!r}, {b.path.name} has {val!r}"
                )
            if val or col not in dst:
                dst[col] = val
    column_source = {**a.column_source, **b.column_source}
    return SupplementTable(
        kind=a.kind,
        path=a.path,  # placeholder; per-file provenance is kept in the bundle's file list
        sha256=a.sha256,
        row_count=a.row_count + b.row_count,
        fact_columns=fact_columns,
        unknown_columns=tuple(sorted(set(a.unknown_columns) | set(b.unknown_columns))),
        provenance=a.provenance,
        as_of=a.as_of,
        rows=rows,
        duplicate_keys=tuple(set(a.duplicate_keys) | set(b.duplicate_keys)),
        adapter=a.adapter,
        column_source=column_source,
    )


@dataclass
class SupplementBundle:
    """Loaded supplement tables (one merged table per kind) plus the per-file record."""

    tables: dict[str, SupplementTable] = field(default_factory=dict)
    files: list[SupplementTable] = field(default_factory=list)

    @classmethod
    def load(cls, specs: Sequence[SupplementFileSpec]) -> SupplementBundle:
        files: list[SupplementTable] = []
        tables: dict[str, SupplementTable] = {}
        for spec in specs:
            table = _load_table(spec)
            files.append(table)
            existing = tables.get(table.kind.name)
            # Several files may target the same kind (e.g. a provider-native export plus a
            # hand-authored file for the facts the adapter leaves out): merge them.
            tables[table.kind.name] = _merge_tables(existing, table) if existing else table
        return cls(tables=tables, files=files)

    def get(self, kind_name: str) -> SupplementTable | None:
        return self.tables.get(kind_name)

    def __bool__(self) -> bool:
        return bool(self.tables)

    def manifest_entries(self) -> list[dict]:
        # One entry per input file, so each keeps its own sha256 / adapter / provenance.
        return [t.manifest_entry() for t in self.files]

    def structural_diagnostics(self) -> list[Diagnostic]:
        """Per-file structural findings (duplicates, unknown columns)."""
        out: list[Diagnostic] = []
        for table in self.files:
            name = table.kind.name
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
