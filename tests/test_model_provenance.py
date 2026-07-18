"""The FOCUS model provenance manifest is verified in the default test suite (CI gate).

Runs the standard-library verifier against the committed artifacts, checks the JSON Schema is
well-formed, and exercises the `partial` -> `complete` gate so the release contract cannot silently
regress.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MANIFEST = _ROOT / "src" / "focus_data_toolkit" / "model" / "model_provenance.json"
_SCHEMA = _ROOT / "schema" / "model_provenance.schema.json"

sys.path.insert(0, str(_ROOT / "scripts"))
from verify_model_provenance import verify  # noqa: E402


def test_committed_provenance_verifies():
    errors = verify(_MANIFEST)
    assert errors == [], "provenance verification failed:\n" + "\n".join(errors)


def test_manifest_status_is_partial_and_honest():
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    # The workbook is not committed/hashed, so the status must be `partial` and the source hash null
    # (no overclaiming a `complete`, fully-reproducible provenance we cannot demonstrate).
    assert manifest["provenance_status"] == "partial"
    assert manifest["source"]["artifact_sha256"] is None
    assert manifest["source"]["license"] == "CC-BY-4.0"
    assert manifest["source"]["license_verified"] is True


def test_schema_is_wellformed():
    # jsonschema is a declared dev dependency, so this runs (and fails loudly) in CI — it does not
    # silently skip. A broken schema must not be able to merge.
    import jsonschema

    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    assert schema["type"] == "object"
    jsonschema.validators.validator_for(schema).check_schema(schema)


def test_instance_matches_schema():
    import jsonschema

    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    # format_checker so `format: date` is enforced (jsonschema ignores formats otherwise).
    jsonschema.validate(manifest, schema, format_checker=jsonschema.FormatChecker())


def test_gate_complete_rejects_invalid_retrieved_date(tmp_path):
    # `complete` with a non-date artifact_retrieved must be rejected (truthiness is not enough).
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    manifest["provenance_status"] = "complete"
    manifest["source"]["artifact_sha256"] = "a" * 64
    manifest["source"]["artifact_retrieved"] = "not-a-date"
    forged = tmp_path / "model_provenance.json"
    forged.write_text(json.dumps(manifest), encoding="utf-8")
    errors = verify(forged)
    assert any("artifact_retrieved" in e for e in errors), errors


def test_verify_rejects_path_escape(tmp_path):
    # A manifest pointing an artifact path outside the repository must be rejected, even if the
    # hash is well-formed — otherwise the gate could "verify" an external file.
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    manifest["output"]["path"] = "../../../../nonexistent_escape.json"
    forged = tmp_path / "model_provenance.json"
    forged.write_text(json.dumps(manifest), encoding="utf-8")
    errors = verify(forged)
    assert any("escapes the repository" in e for e in errors), errors


def test_gate_complete_requires_source_hash(tmp_path):
    # Flip the real (partial) manifest to `complete` without adding a source hash: the gate must
    # reject it. This proves a stable release cannot claim `complete` provenance for free.
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    manifest["provenance_status"] = "complete"
    forged = tmp_path / "model_provenance.json"
    forged.write_text(json.dumps(manifest), encoding="utf-8")
    errors = verify(forged)
    assert any("artifact_sha256" in e for e in errors), errors


def test_gate_partial_rejects_stray_source_hash(tmp_path):
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    manifest["source"]["artifact_sha256"] = "0" * 64  # partial should keep this null
    forged = tmp_path / "model_provenance.json"
    forged.write_text(json.dumps(manifest), encoding="utf-8")
    errors = verify(forged)
    assert any("partial" in e for e in errors), errors


def test_complete_flips_status_after_byte_identical_reextraction(tmp_path):
    from verify_model_provenance import complete

    staged_manifest = tmp_path / "model_provenance.json"
    staged_manifest.write_text(_MANIFEST.read_text(encoding="utf-8"), encoding="utf-8")
    workbook = tmp_path / "focus_1_4_data_model.xlsx"
    workbook.write_bytes(b"pretend workbook bytes")
    committed_model = (
        _ROOT / "src" / "focus_data_toolkit" / "model" / "focus_1_4_model.json"
    ).read_bytes()

    errors = complete(
        workbook,
        retrieved="2026-07-18",
        manifest_path=staged_manifest,
        reextract=lambda _wb: committed_model,  # byte-identical re-extraction
    )
    assert errors == []
    updated = json.loads(staged_manifest.read_text(encoding="utf-8"))
    assert updated["provenance_status"] == "complete"
    assert updated["source"]["artifact_retrieved"] == "2026-07-18"
    import hashlib

    assert updated["source"]["artifact_sha256"] == hashlib.sha256(
        workbook.read_bytes()
    ).hexdigest()
    from verify_model_provenance import verify as _verify

    assert _verify(staged_manifest) == []


def test_complete_refuses_a_divergent_workbook_and_touches_nothing(tmp_path):
    from verify_model_provenance import complete

    staged_manifest = tmp_path / "model_provenance.json"
    original = _MANIFEST.read_text(encoding="utf-8")
    staged_manifest.write_text(original, encoding="utf-8")
    workbook = tmp_path / "wb.xlsx"
    workbook.write_bytes(b"different revision")

    errors = complete(
        workbook,
        manifest_path=staged_manifest,
        reextract=lambda _wb: b"NOT the committed model",
    )
    assert errors and "byte-for-byte" in errors[0]
    assert staged_manifest.read_text(encoding="utf-8") == original  # untouched
    assert not list(tmp_path.glob("*.completing"))


def test_complete_rejects_bad_date_and_missing_workbook(tmp_path):
    from verify_model_provenance import complete

    missing = tmp_path / "nope.xlsx"
    assert complete(missing, manifest_path=_MANIFEST) == [f"workbook not found: {missing}"]
    workbook = tmp_path / "wb.xlsx"
    workbook.write_bytes(b"x")
    errors = complete(workbook, retrieved="18/07/2026", manifest_path=_MANIFEST)
    assert errors and "ISO date" in errors[0]
