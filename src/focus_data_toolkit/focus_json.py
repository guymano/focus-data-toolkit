"""Deterministic JSON serialization for FOCUS JSON-typed columns.

FOCUS types several JSON object properties as ``Numeric`` / ``Decimal`` (e.g.
``AllocatedMethodDetails.AllocatedRatio``, ``ContractApplied.*AppliedCost``).
Those MUST be serialized as JSON **numbers**, not quoted strings. To keep exact
decimals (no float rounding) and byte-reproducible output, numeric properties are
emitted by inserting their exact decimal text as a raw JSON number token rather
than round-tripping through ``float``.

:class:`JsonNumber` tags text that originated from a real JSON number token (use it
as ``json.loads``'s ``parse_float``/``parse_int`` hook). It lets a parser tell a
JSON number apart from a quoted numeric string after parsing, and lets ``_encode``
re-emit such values as raw number literals (preserving custom numeric properties
across a parse/serialize round-trip).
"""

from __future__ import annotations

import json
import re

# JSON number grammar restricted to the FOCUS NumericFormat shape: no leading zeros,
# E-notation with a negative-only exponent sign, no leading '+'.
_JSON_NUMBER_RE = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:E-?\d+)?")


class JsonNumber(str):
    """Exact text of a value parsed from a JSON **number** token (not a string)."""

    __slots__ = ()


def is_json_number_literal(text: str) -> bool:
    """True if ``text`` is a JSON number literal (FOCUS numeric shape)."""
    return bool(_JSON_NUMBER_RE.fullmatch(text))


def _raw_number(text: str, key: str | None = None) -> str:
    if not is_json_number_literal(text):
        where = f"property {key!r}" if key else "value"
        raise ValueError(f"numeric {where} is not a JSON number: {text!r}")
    return text


def _encode(value: object, numeric_keys: frozenset[str]) -> str:
    if isinstance(value, dict):
        parts = []
        for key, val in value.items():
            if key in numeric_keys and isinstance(val, str):
                parts.append(f"{json.dumps(key)}:{_raw_number(val, key)}")
            else:
                parts.append(f"{json.dumps(key)}:{_encode(val, numeric_keys)}")
        return "{" + ",".join(parts) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_encode(v, numeric_keys) for v in value) + "]"
    if isinstance(value, JsonNumber):
        return _raw_number(value)
    return json.dumps(value)


def dumps_object(obj: dict, *, numeric_keys: frozenset[str] = frozenset()) -> str:
    """Serialize ``obj`` to compact JSON, emitting ``numeric_keys`` as JSON numbers.

    ``numeric_keys`` applies by key name at any nesting depth, so numeric
    properties inside an ``Elements`` array are emitted unquoted too. Values
    tagged :class:`JsonNumber` are emitted as raw number literals regardless of key.
    """
    return _encode(obj, numeric_keys)
