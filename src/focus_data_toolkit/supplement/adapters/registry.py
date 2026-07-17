"""Provider-native supplement adapters — translate documented cloud export formats.

The primary client journey is a cloud-provider customer holding FOCUS 1.2/1.3 **plus
native provider artifacts** (invoice exports, reservation/commitment inventories) from
AWS, Azure or GCP — not hand-authored FOCUS-named files. An **adapter** recognizes such a
format by its native field names and translates each row into a canonical supplement kind
(see :mod:`focus_data_toolkit.supplement.kinds`). The translated rows then flow through the
*unchanged* supplement validation — adapters never bypass it.

An adapter is **data, not code**: a vendored JSON mapping table (``adapters/*.json``) with a
provenance block (official-doc URL, the model artifact the field names came from, retrieval
date). The mapping only ever claims fields the vendored table describes; a value it cannot
determine is simply not emitted (the client supplies it, and coverage reporting shows the
residual gap). A file matching no adapter falls back to the generic FOCUS-named path.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from focus_data_toolkit.supplement.kinds import SUPPLEMENT_KINDS
from focus_data_toolkit.supplement.spec import SupplementError

ADAPTERS_DIR = Path(__file__).resolve().parent
PROVENANCE_FILENAME = "adapters_provenance.json"

# FOCUS Date/Time: YYYY-MM-DDTHH:mm:ss[.fff]Z (UTC). We normalize provider timestamps to it.
_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}$")


class AdapterError(SupplementError):
    """An adapter mapping table is malformed or produces an invalid row."""


def _to_utc_datetime(value: str) -> str:
    """Normalize a provider timestamp to the FOCUS UTC form; pass through if unparseable.

    An unparseable value is returned unchanged so the supplement validator flags it with a
    precise diagnostic rather than the adapter silently dropping or guessing it.
    """
    text = value.strip()
    if not text:
        return ""
    if _DATE_ONLY.fullmatch(text):
        return f"{text}T00:00:00Z"
    candidate = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return text
    # Emit UTC 'Z'. Aware -> convert to UTC; naive -> assume already UTC.
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    micro = f".{dt.microsecond:06d}".rstrip("0") if dt.microsecond else ""
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + micro + "Z"


_TRANSFORMS = {
    "date_to_utc": _to_utc_datetime,
}


@dataclass(frozen=True)
class FieldMapping:
    """How one FOCUS target column is produced from the native row."""

    target: str
    source: str | None = None  # native field name (dot-path for nested JSON)
    const: str | None = None  # fixed value (an invariant fact of the product type)
    value_map: Mapping[str, str] | None = None  # native value -> FOCUS value
    default: str | None = None  # value_map fallback (None = drop when unmapped)
    transform: str | None = None  # a name in _TRANSFORMS applied to the source value

    def produce(self, row: Mapping[str, str]) -> str | None:
        if self.const is not None:
            return self.const
        assert self.source is not None
        raw = (row.get(self.source) or "").strip()
        if self.value_map is not None:
            if not raw:
                return None
            mapped = self.value_map.get(raw, self.default)
            return mapped  # None -> unmapped, drop (client supplies / coverage gap)
        if self.transform:
            raw = _TRANSFORMS[self.transform](raw)
        return raw or None


@dataclass(frozen=True)
class Adapter:
    """A vendored translation from one native provider export to a supplement kind."""

    name: str
    version: str
    target_kind: str
    detect_all_of: tuple[str, ...]
    detect_any_of: tuple[str, ...]
    fields: tuple[FieldMapping, ...]
    provenance: Mapping[str, str]

    def matches(self, header: Sequence[str]) -> bool:
        present = set(header)
        if not set(self.detect_all_of) <= present:
            return False
        return not self.detect_any_of or bool(present & set(self.detect_any_of))

    def translate(self, rows: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
        """Translate native rows into canonical-kind rows (FOCUS column names)."""
        out: list[dict[str, str]] = []
        for row in rows:
            translated: dict[str, str] = {}
            for field in self.fields:
                value = field.produce(row)
                if value is not None and value != "":
                    translated[field.target] = value
            out.append(translated)
        return out

    @property
    def source_tag(self) -> str:
        return f"{self.name}@{self.version}"


def _parse_adapter(name: str, data: dict) -> Adapter:
    try:
        kind = data["target_kind"]
        if kind not in SUPPLEMENT_KINDS:
            raise AdapterError(f"adapter {name!r}: unknown target_kind {kind!r}")
        fields = tuple(
            FieldMapping(
                target=f["target"],
                source=f.get("source"),
                const=f.get("const"),
                value_map=f.get("value_map"),
                default=f.get("default"),
                transform=f.get("transform"),
            )
            for f in data["fields"]
        )
        adapter = Adapter(
            name=data["adapter"],
            version=str(data["version"]),
            target_kind=kind,
            detect_all_of=tuple(data["detect"].get("all_of", ())),
            detect_any_of=tuple(data["detect"].get("any_of", ())),
            fields=fields,
            provenance=data.get("provenance", {}),
        )
    except (KeyError, TypeError) as exc:
        raise AdapterError(f"adapter {name!r}: malformed mapping table ({exc})") from exc
    # The adapter must produce its kind's join keys.
    produced = {f.target for f in adapter.fields}
    missing = [k for k in SUPPLEMENT_KINDS[kind].join_keys if k not in produced]
    if missing:
        raise AdapterError(
            f"adapter {adapter.name!r} does not produce join key(s) {', '.join(missing)}"
        )
    # Every transform / target must be known.
    for field in adapter.fields:
        if field.transform and field.transform not in _TRANSFORMS:
            raise AdapterError(
                f"adapter {adapter.name!r}: unknown transform {field.transform!r}"
            )
    return adapter


@lru_cache(maxsize=1)
def load_adapters() -> dict[str, Adapter]:
    """Load every vendored adapter mapping table (keyed by adapter name)."""
    adapters: dict[str, Adapter] = {}
    for path in sorted(ADAPTERS_DIR.glob("*.json")):
        if path.name == PROVENANCE_FILENAME:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        adapter = _parse_adapter(path.stem, data)
        adapters[adapter.name] = adapter
    return adapters


def get_adapter(name: str) -> Adapter:
    adapter = load_adapters().get(name)
    if adapter is None:
        known = ", ".join(sorted(load_adapters())) or "(none)"
        raise AdapterError(f"unknown adapter {name!r}; known adapters: {known}")
    return adapter


def detect_adapter(header: Sequence[str]) -> Adapter | None:
    """Return the single adapter matching ``header``; ambiguity raises, no match is ``None``."""
    matches = [a for a in load_adapters().values() if a.matches(header)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return None
    names = ", ".join(sorted(a.name for a in matches))
    raise AdapterError(
        f"provider export header is ambiguous between adapters: {names}; "
        "force one with FILE:<adapter-name>"
    )


def adapter_provenance() -> dict:
    """The vendored adapter provenance manifest (source docs + hashes)."""
    return json.loads((ADAPTERS_DIR / PROVENANCE_FILENAME).read_text(encoding="utf-8"))


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
