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

**Completing the provenance** (owner action, needs the source workbook + ``openpyxl``): the
``--complete`` mode hashes the supplied workbook, re-runs the pinned extractor against it in an
isolated temporary tree, and only if the committed model is reproduced **byte-for-byte** flips
``provenance_status`` to ``complete`` (recording ``source.artifact_sha256`` /
``source.artifact_retrieved``) — then re-verifies the whole manifest. A workbook that does not
reproduce the committed model aborts without touching anything.

    python scripts/verify_model_provenance.py --complete /path/to/focus_1_4_data_model.xlsx
"""

from __future__ import annotations

import argparse
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


def _reextract(workbook: Path) -> bytes:
    """Run the pinned extractor against ``workbook`` in an isolated temp tree; return the bytes.

    The extractor writes repo-relative to its own location, so it is copied (together with the
    ServiceSubcategory supplement it reads) into a temporary tree mirroring the repo layout —
    the committed model is never touched by a completion attempt.
    """
    import shutil
    import subprocess
    import sys
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "tools").mkdir()
        model_dir = tmp / "src" / "focus_data_toolkit" / "model"
        model_dir.mkdir(parents=True)
        shutil.copy2(_REPO / "tools" / "extract_focus_1_4_model.py", tmp / "tools")
        shutil.copy2(
            _REPO / "src" / "focus_data_toolkit" / "model" / "focus_1_4_servicesubcategory.json",
            model_dir,
        )
        proc = subprocess.run(
            [sys.executable, str(tmp / "tools" / "extract_focus_1_4_model.py"), str(workbook)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip()
            raise RuntimeError(f"extractor failed (is openpyxl installed?): {detail}")
        return (model_dir / "focus_1_4_model.json").read_bytes()


def complete(
    workbook: Path,
    *,
    retrieved: str | None = None,
    manifest_path: Path = _MANIFEST,
    reextract=_reextract,
) -> list[str]:
    """Flip the manifest to ``provenance_status = 'complete'`` after full verification.

    Returns a list of problems; empty means the manifest was updated and re-verified. Nothing
    is written unless the supplied workbook reproduces the committed model byte-for-byte.
    """
    if not workbook.is_file():
        return [f"workbook not found: {workbook}"]
    retrieved = retrieved or _dt.date.today().isoformat()
    if not _is_iso_date(retrieved):
        return [f"--retrieved must be an ISO date (YYYY-MM-DD), got {retrieved!r}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read/parse {manifest_path}: {exc}"]

    try:
        produced = reextract(workbook)
    except RuntimeError as exc:
        return [str(exc)]
    committed = (_REPO / manifest["output"]["path"]).read_bytes()
    if produced != committed:
        return [
            "re-extraction from the supplied workbook does NOT reproduce the committed model "
            "byte-for-byte — the workbook revision differs from the one the model was built "
            "from. Do not complete provenance with this artifact; re-run "
            "tools/extract_focus_1_4_model.py and review the model diff instead."
        ]

    manifest["provenance_status"] = "complete"
    manifest["source"]["artifact_sha256"] = _sha256(workbook)
    manifest["source"]["artifact_retrieved"] = retrieved
    manifest["reproducibility"]["limitation"] = (
        "None for the model chain: the source workbook is hashed (source.artifact_sha256) and "
        "the committed output was reproduced byte-for-byte from it by the pinned extractor at "
        "completion time. The workbook itself is still not redistributed; obtain it from the "
        "FinOps Foundation and verify it against source.artifact_sha256."
    )
    manifest["notes"] = [
        "provenance_status was set to 'complete' by scripts/verify_model_provenance.py "
        "--complete: the source workbook was hashed and the committed model was reproduced "
        "byte-for-byte from it with the pinned extractor."
    ]
    # Verify the updated manifest fully before it replaces the committed one, so a completion
    # can never leave a manifest that fails its own gate.
    import os

    staged = manifest_path.with_suffix(".json.completing")
    staged.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    errors = verify(staged)
    if errors:
        staged.unlink(missing_ok=True)
        return errors
    os.replace(staged, manifest_path)
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--complete",
        metavar="WORKBOOK",
        help="hash WORKBOOK, verify byte-for-byte re-extraction, and set "
        "provenance_status=complete in the manifest",
    )
    parser.add_argument(
        "--retrieved",
        metavar="YYYY-MM-DD",
        help="retrieval date recorded with --complete (defaults to today)",
    )
    args = parser.parse_args(argv)

    if args.complete:
        errors = complete(Path(args.complete), retrieved=args.retrieved)
        if errors:
            print("FOCUS model provenance completion FAILED (manifest untouched unless noted):")
            for err in errors:
                print(f"  - {err}")
            return 1
        print(
            f"FOCUS model provenance COMPLETED and re-verified OK "
            f"({_MANIFEST.relative_to(_REPO)})."
        )
        return 0

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
