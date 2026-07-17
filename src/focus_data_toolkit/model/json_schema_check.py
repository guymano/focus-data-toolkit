"""Validate FOCUS JSON-object column values against the vendored official schemas.

The FOCUS specification publishes normative JSON Schemas (draft 2020-12) for its
JSON-object columns under ``specification/schemas/datasets/``. Those files are
vendored verbatim in ``model/json_schemas/`` (see ``json_schemas_provenance.json``)
and evaluated here with a small interpreter covering exactly the keyword subset the
official schemas use — no runtime dependency, and the vendored files (not hand-coded
rules) remain the single source of truth.

Supported keywords: ``$ref`` (``#/$defs/...``), ``type`` (incl. type lists),
``properties``, ``patternProperties``, ``additionalProperties`` (boolean),
``required``, ``items``, ``minItems`` / ``maxItems``, ``contains``, ``enum``,
``const``, ``minimum`` / ``maximum``, ``allOf`` / ``anyOf`` / ``oneOf`` / ``not``,
``if`` / ``then``, and boolean schemas. An unsupported keyword raises — a vendored
schema update that outgrows the subset must fail loudly, never silently pass.
"""

from __future__ import annotations

import json
import math
import re
from functools import cache
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent / "json_schemas"
PROVENANCE_PATH = SCHEMA_DIR / "json_schemas_provenance.json"

# FOCUS column -> vendored official schema file.
OFFICIAL_SCHEMA_COLUMNS: dict[str, str] = {
    "ContractApplied": "contractappliedobjectschema.json",
    "AllocatedMethodDetails": "allocatedmethoddetailsobjectschema.json",
    "CommitmentProgramEligibilityDetails": "commitmentprogrameligibilitydetailsobjectschema.json",
    "ContractCommitmentApplicability": "contractcommitmentapplicabilityobjectschema.json",
}

# Keywords the interpreter evaluates, plus annotations it may safely ignore.
_HANDLED = frozenset({
    "$ref", "type", "properties", "patternProperties", "additionalProperties",
    "required", "items", "minItems", "maxItems", "contains", "enum", "const",
    "minimum", "maximum", "allOf", "anyOf", "oneOf", "not", "if", "then",
})
_ANNOTATIONS = frozenset({"$schema", "$id", "$defs", "title", "description", "default"})

def _is_number(v: object) -> bool:
    # Standard JSON has no NaN/Infinity; Python's parser may still produce them from
    # the non-standard constants, so a non-finite float is never a schema "number".
    if isinstance(v, bool) or not isinstance(v, int | float):
        return False
    return math.isfinite(v)


_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "number": _is_number,
    "integer": lambda v: _is_number(v) and (isinstance(v, int) or v.is_integer()),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class UnsupportedSchemaKeyword(ValueError):
    """A vendored schema uses a keyword outside the interpreter's subset."""


@cache
def load_official_schema(filename: str) -> dict:
    schema = json.loads((SCHEMA_DIR / filename).read_text(encoding="utf-8"))
    _assert_supported(schema)
    return schema


def _assert_supported(schema: object) -> None:
    if isinstance(schema, bool):
        return
    if isinstance(schema, dict):
        for key, sub in schema.items():
            if key in _ANNOTATIONS:
                continue
            if key not in _HANDLED:
                raise UnsupportedSchemaKeyword(f"unsupported JSON Schema keyword {key!r}")
            if key in ("properties", "patternProperties"):
                for nested in sub.values():
                    _assert_supported(nested)
            elif key in ("allOf", "anyOf", "oneOf"):
                for nested in sub:
                    _assert_supported(nested)
            elif key in ("items", "contains", "not", "if", "then", "additionalProperties"):
                _assert_supported(sub)
        for nested in schema.get("$defs", {}).values():
            _assert_supported(nested)


def check_against_official_schema(column: str, obj: object) -> list[str]:
    """Return the official-schema violations for ``obj`` (empty list = conformant)."""
    schema = load_official_schema(OFFICIAL_SCHEMA_COLUMNS[column])
    return _validate(obj, schema, schema, "$")


def _resolve_ref(root: dict, ref: str) -> dict:
    if not ref.startswith("#/"):  # the official schemas only use local refs
        raise UnsupportedSchemaKeyword(f"unsupported $ref target {ref!r}")
    node: object = root
    for part in ref[2:].split("/"):
        node = node[part]  # type: ignore[index]
    return node  # type: ignore[return-value]


def _type_ok(value: object, spec: str | list[str]) -> bool:
    names = [spec] if isinstance(spec, str) else spec
    return any(_TYPE_CHECKS[n](value) for n in names)


def _json_equal(a: object, b: object) -> bool:
    # JSON equality: booleans are not numbers (Python's bool == int would conflate them).
    if isinstance(a, bool) is not isinstance(b, bool):
        return False
    return a == b


def _validate(value: object, schema: object, root: dict, path: str) -> list[str]:
    if schema is True:
        return []
    if schema is False:
        return [f"{path}: not permitted here"]
    assert isinstance(schema, dict)

    if "$ref" in schema:
        ref_errors = _validate(value, _resolve_ref(root, schema["$ref"]), root, path)
        if ref_errors:
            return ref_errors

    spec_type = schema.get("type")
    if spec_type is not None and not _type_ok(value, spec_type):
        return [f"{path}: expected type {spec_type}"]

    errors: list[str] = []
    if "enum" in schema and not any(_json_equal(value, v) for v in schema["enum"]):
        errors.append(f"{path}: value not in {schema['enum']}")
    if "const" in schema and not _json_equal(value, schema["const"]):
        errors.append(f"{path}: expected constant {schema['const']!r}")

    for sub in schema.get("allOf", ()):
        errors.extend(_validate(value, sub, root, path))
    if "anyOf" in schema and all(_validate(value, sub, root, path) for sub in schema["anyOf"]):
        errors.append(f"{path}: matches none of the allowed forms (anyOf)")
    if "oneOf" in schema:
        matched = sum(1 for sub in schema["oneOf"] if not _validate(value, sub, root, path))
        if matched != 1:
            errors.append(f"{path}: must match exactly one allowed form (matched {matched})")
    if "not" in schema and not _validate(value, schema["not"], root, path):
        errors.append(f"{path}: matches a prohibited form")
    if "if" in schema and not _validate(value, schema["if"], root, path):
        errors.extend(_validate(value, schema.get("then", True), root, path))

    if isinstance(value, dict):
        errors.extend(_validate_object(value, schema, root, path))
    elif isinstance(value, list):
        errors.extend(_validate_array(value, schema, root, path))
    elif isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} is above maximum {schema['maximum']}")
    return errors


def _validate_object(value: dict, schema: dict, root: dict, path: str) -> list[str]:
    errors: list[str] = []
    for req in schema.get("required", ()):
        if req not in value:
            errors.append(f"{path}: required property {req!r} is missing")
    properties = schema.get("properties", {})
    patterns = {re.compile(p): s for p, s in schema.get("patternProperties", {}).items()}
    additional = schema.get("additionalProperties", True)
    for key, item in value.items():
        matched = False
        if key in properties:
            matched = True
            errors.extend(_validate(item, properties[key], root, f"{path}.{key}"))
        for pattern, sub in patterns.items():
            if pattern.search(key):
                matched = True
                errors.extend(_validate(item, sub, root, f"{path}.{key}"))
        if not matched:
            errors.extend(_validate(item, additional, root, f"{path}.{key}"))
    return errors


def _validate_array(value: list, schema: dict, root: dict, path: str) -> list[str]:
    errors: list[str] = []
    if "minItems" in schema and len(value) < schema["minItems"]:
        errors.append(f"{path}: needs at least {schema['minItems']} item(s)")
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        errors.append(f"{path}: allows at most {schema['maxItems']} item(s)")
    if "items" in schema:
        for i, item in enumerate(value):
            errors.extend(_validate(item, schema["items"], root, f"{path}[{i}]"))
    if "contains" in schema and all(
        _validate(item, schema["contains"], root, path) for item in value
    ):
        errors.append(f"{path}: no item matches the required form (contains)")
    return errors
