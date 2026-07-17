"""Supplement file specs — how supplement inputs are named and described.

Two equivalent surfaces:

* repeated ``--supplement FILE[:KIND]`` arguments;
* a bundle directory with a ``supplements.json`` manifest, whose free-text
  ``provenance`` / ``as_of`` fields are carried into the conversion manifest so every
  ``ENRICHED`` value stays human-auditable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SUPPLEMENTS_MANIFEST = "supplements.json"
SUPPLEMENT_BUNDLE_FORMAT = "1"


class SupplementError(ValueError):
    """A supplement input cannot be used (unknown kind, unreadable file, bad manifest)."""


@dataclass(frozen=True)
class SupplementFileSpec:
    """One supplement input file, before loading."""

    path: Path
    kind: str | None = None  # forced kind name; None = detect from the header
    provenance: str | None = None  # free text: where the client got this file
    as_of: str | None = None  # free text: extraction date the client declares


def parse_supplement_arg(arg: str) -> SupplementFileSpec:
    """Parse a ``FILE[:KIND]`` CLI argument (the last ``:`` splits only if KIND is known-ish)."""
    path, sep, kind = arg.rpartition(":")
    # A Windows drive letter ("C:\...") or plain path without kind: no split.
    if not sep or not path or ("/" in kind or "\\" in kind or len(path) == 1):
        return SupplementFileSpec(path=Path(arg))
    return SupplementFileSpec(path=Path(path), kind=kind)


def load_bundle_dir(directory: str | Path) -> list[SupplementFileSpec]:
    """Read ``supplements.json`` in ``directory`` and return its file specs."""
    root = Path(directory)
    manifest_path = root / SUPPLEMENTS_MANIFEST
    if not manifest_path.is_file():
        raise SupplementError(f"no {SUPPLEMENTS_MANIFEST} found in {root}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SupplementError(f"{manifest_path}: invalid JSON: {exc}") from exc
    if manifest.get("supplement_format") != SUPPLEMENT_BUNDLE_FORMAT:
        raise SupplementError(
            f"{manifest_path}: unsupported supplement_format "
            f"{manifest.get('supplement_format')!r} (expected {SUPPLEMENT_BUNDLE_FORMAT!r})"
        )
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise SupplementError(f"{manifest_path}: 'files' must be a non-empty list")
    specs: list[SupplementFileSpec] = []
    for entry in files:
        if not isinstance(entry, dict) or "path" not in entry:
            raise SupplementError(f"{manifest_path}: each file entry needs a 'path'")
        specs.append(
            SupplementFileSpec(
                path=root / entry["path"],
                kind=entry.get("kind"),
                provenance=entry.get("provenance"),
                as_of=entry.get("as_of"),
            )
        )
    return specs
