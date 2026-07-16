"""Deterministic JSON serialization for FOCUS JSON-typed columns.

FOCUS types several JSON object properties as ``Numeric`` / ``Decimal`` (e.g.
``AllocatedMethodDetails.AllocatedRatio``, ``ContractApplied.*AppliedCost``).
Those MUST be serialized as JSON **numbers**, not quoted strings. To keep exact
decimals (no float rounding) and byte-reproducible output, numeric properties are
emitted by inserting their exact decimal text as a raw JSON number token rather
than round-tripping through ``float``.
"""

from __future__ import annotations

import json
import re

# JSON number grammar restricted to the FOCUS NumericFormat shape (E-notation with
# a negative-only exponent sign; no leading '+').
_JSON_NUMBER_RE = re.compile(r"-?(?:0|[1-9]\d*|\d+)(?:\.\d+)?(?:E-?\d+)?")


def is_json_number_literal(text: str) -> bool:
    """True if ``text`` is a JSON number literal (FOCUS numeric shape)."""
    return bool(_JSON_NUMBER_RE.fullmatch(text))


def _encode(value: object, numeric_keys: frozenset[str]) -> str:
    if isinstance(value, dict):
        parts = []
        for key, val in value.items():
            if key in numeric_keys and isinstance(val, str):
                if not is_json_number_literal(val):
                    raise ValueError(f"numeric property {key!r} is not a JSON number: {val!r}")
                parts.append(f"{json.dumps(key)}:{val}")
            else:
                parts.append(f"{json.dumps(key)}:{_encode(val, numeric_keys)}")
        return "{" + ",".join(parts) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_encode(v, numeric_keys) for v in value) + "]"
    return json.dumps(value)


def dumps_object(obj: dict, *, numeric_keys: frozenset[str] = frozenset()) -> str:
    """Serialize ``obj`` to compact JSON, emitting ``numeric_keys`` as JSON numbers.

    ``numeric_keys`` applies by key name at any nesting depth, so numeric
    properties inside an ``Elements`` array are emitted unquoted too.
    """
    return _encode(obj, numeric_keys)
