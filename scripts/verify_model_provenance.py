#!/usr/bin/env python3
"""Verify the FOCUS model provenance manifest against the committed model artifacts.

Release/CI gate for ``src/focus_data_toolkit/model/model_provenance.json``. Standard-library only
so it runs anywhere the package does (no extra install). It checks, and fails on any mismatch:

* the manifest is well-formed and structurally valid (required fields, types, enums, sha256 shape);
* ``output.sha256`` / ``output.bytes`` match the committed model JSON;
* ``generator.script_sha256`` matches the committed extractor (the process of record);
* every ``supplements[].sha256`` matches its committed file;
* the ``partial`` -> ``complete`` gate: a ``complete`` status must hash the source artifact.

If the optional ``jsonschema`` package is importable it *also* validates the manifest against
``schema/model_provenance.schema.json`` (the formal contract); otherwise that step is skipped with
a note (the structural checks above still run).

    python scripts/verify_model_provenance.py        # exit 0 on success, 1 on any problem
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_MANIFEST = _REPO / "src" / "focus_data_toolkit" / "model" / "model_provenance.json"
_SCHEMA = _REPO / "schema" / "model_provenance.schema.json"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_iso_date(value: str) -> bool:
    try:
        _dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _check_hash(errors: list[str], label: str, rel_path: str, expected: str) -> None:
    """Confirm ``expected`` is a sha256 and matches the file at ``rel_path`` (repo-relative)."""
    if not isinstance(expected, str) or not _SHA256.match(expected):
        errors.append(f"{label}: sha256 is not a 64-hex string: {expected!r}")
        return
    # Reject absolute paths and `..` escapes: the manifest must only vouch for files *inside* the
    # repository, so a doctored manifest cannot claim to have verified some external file.
    target = (_REPO / rel_path).resolve()
    if not target.is_relative_to(_REPO):
        errors.append(f"{label}: path escapes the repository: {rel_path!r}")
        return
    if not target.is_file():
        errors.append(f"{label}: file not found: {rel_path}")
        return
    actual = _sha256(target)
    if actual != expected:
        errors.append(f"{label}: sha256 mismatch for {rel_path}\n    manifest: {expected}\n    actual:   {actual}")


def verify(manifest_path: Path = _MANIFEST) -> list[str]:
    """Return a list of human-readable problems; empty means the provenance is verified."""
    errors: list[str] = []

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read/parse {manifest_path}: {exc}"]

    # --- structural checks (mirror the JSON Schema's key rules, dependency-free) ------------- #
    for field in ("schema_version", "focus_version", "provenance_status", "source", "generator", "output"):
        if field not in manifest:
            errors.append(f"missing required top-level field: {field!r}")
    if errors:
        return errors  # nothing else is safe to inspect

    status = manifest["provenance_status"]
    if status not in ("partial", "complete"):
        errors.append(f"provenance_status must be 'partial' or 'complete', got {status!r}")

    source = manifest["source"]
    generator = manifest["generator"]
    output = manifest["output"]

    # --- artifact hash verification (the point of the gate) ---------------------------------- #
    _check_hash(errors, "output", output.get("path", ""), output.get("sha256", ""))
    _check_hash(errors, "generator.script", generator.get("script", ""), generator.get("script_sha256", ""))
    for i, supp in enumerate(manifest.get("supplements", [])):
        _check_hash(errors, f"supplements[{i}]", supp.get("path", ""), supp.get("sha256", ""))

    # output.bytes must match the real file size.
    out_path = _REPO / output.get("path", "")
    if out_path.is_file():
        actual_bytes = out_path.stat().st_size
        if output.get("bytes") != actual_bytes:
            errors.append(f"output.bytes {output.get('bytes')!r} != actual {actual_bytes}")

    # --- the partial -> complete gate (R8bis) ------------------------------------------------ #
    if status == "complete":
        art = source.get("artifact_sha256")
        if not isinstance(art, str) or not _SHA256.match(art):
            errors.append("provenance_status 'complete' requires source.artifact_sha256 (64-hex)")
        retrieved = source.get("artifact_retrieved")
        if not isinstance(retrieved, str) or not _is_iso_date(retrieved):
            errors.append(
                "provenance_status 'complete' requires source.artifact_retrieved as an ISO date "
                f"(YYYY-MM-DD), got {retrieved!r}"
            )
        if source.get("license_verified") is not True:
            errors.append("provenance_status 'complete' requires source.license_verified = true")
    else:  # partial: the source artifact is intentionally un-hashed
        if source.get("artifact_sha256") not in (None,):
            errors.append("provenance_status 'partial' should leave source.artifact_sha256 = null")

    # --- optional formal JSON Schema validation --------------------------------------------- #
    errors.extend(_schema_validate(manifest))
    return errors


def _schema_validate(manifest: dict) -> list[str]:
    """Validate against the JSON Schema if ``jsonschema`` is installed; else skip (return [])."""
    try:
        import jsonschema  # type: ignore[import-untyped]
    except ImportError:
        return []
    try:
        schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read/parse schema {_SCHEMA}: {exc}"]
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    # format_checker so `format: date` is actually enforced (jsonschema ignores formats otherwise).
    validator = validator_cls(schema, format_checker=jsonschema.FormatChecker())
    errors = sorted(validator.iter_errors(manifest), key=lambda e: e.path)
    return [f"schema: {e.json_path}: {e.message}" for e in errors]


def main() -> int:
    errors = verify()
    if errors:
        print("FOCUS model provenance verification FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1
    print(f"FOCUS model provenance verified OK ({_MANIFEST.relative_to(_REPO)}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
