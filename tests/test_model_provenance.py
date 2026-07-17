"""The FOCUS model provenance manifest is verified in the default test suite (CI gate).

Runs the standard-library verifier against the committed artifacts, checks the JSON Schema is
well-formed, and exercises the `partial` -> `complete` gate so the release contract cannot silently
regress.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

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
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    assert schema["type"] == "object"
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validators.validator_for(schema).check_schema(schema)


def test_instance_matches_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    jsonschema.validate(manifest, schema)


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
