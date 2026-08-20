"""Typed ``ContractApplied`` model, parser, validator and 1.3->1.4 migration.

``ContractApplied`` (FOCUS Cost and Usage, JSON Object Format) links a usage row to
the Contract Commitment dataset. Its structure is a top-level ``Elements`` array of
objects. The identifier keys are ``ContractId`` / ``ContractCommitmentId`` in both
versions: the published 1.3 column text carried the uppercase ``ContractID`` /
``ContractCommitmentID`` casing, but FOCUS erratum #3 (the 1.3.0.1 rule model)
corrected it to the ``Id`` casing that 1.4 kept. Parsing a 1.3 value therefore
accepts **both** casings — canonical ``…Id`` per the erratum, and legacy ``…ID``
for files produced against the pre-erratum text — while serialization emits only
the canonical casing. Callers can observe legacy-cased input via the
``legacy_sink`` parameter (it is a compatibility normalization, not conformance).

The three metric keys — ``ContractCommitmentAppliedCost``,
``ContractCommitmentAppliedQuantity``, ``ContractCommitmentAppliedUnit`` — are stable
across versions; the cost/quantity values are Numeric (JSON **numbers**, never quoted
strings). Custom keys (top level or inside elements) MUST be ``x_``-prefixed.

**1.4 metric exclusivity** (``ContractAppliedObjectSchema`` @ ``v1.4``, ``oneOf``):
an element carries *either* ``AppliedCost`` (quantity/unit absent-or-null) *or*
``AppliedQuantity``+``AppliedUnit`` (cost absent-or-null) — never both. FOCUS 1.3
only requires *at least one* form, so a legal 1.3 element may carry all three
metrics; :func:`migrate_1_3_to_1_4` then keeps the **cost** branch (the reconciling
financial amount) and preserves quantity/unit losslessly as the custom keys
``x_ContractCommitmentAppliedQuantity`` / ``x_ContractCommitmentAppliedUnit``.

The internal model is version-neutral; version affects (de)serialization casing and
the 1.4-only exclusivity rule.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from focus_data_toolkit.focus_json import JsonNumber, dumps_object

_METRIC_COST = "ContractCommitmentAppliedCost"
_METRIC_QTY = "ContractCommitmentAppliedQuantity"
_METRIC_UNIT = "ContractCommitmentAppliedUnit"
_METRICS = (_METRIC_COST, _METRIC_QTY, _METRIC_UNIT)
_NUMERIC_METRIC_KEYS = frozenset({_METRIC_COST, _METRIC_QTY})

# Accepted identifier casings per version, canonical first. 1.3 also accepts the
# legacy uppercase-``ID`` casing of the pre-erratum published text (see the module
# docstring); serialization always uses the canonical (first) casing.
_ID_KEYS: dict[str, dict[str, tuple[str, ...]]] = {
    "1.3": {
        "contract": ("ContractId", "ContractID"),
        "commitment": ("ContractCommitmentId", "ContractCommitmentID"),
    },
    "1.4": {"contract": ("ContractId",), "commitment": ("ContractCommitmentId",)},
}
# The 1.4 ContractAppliedObjectSchema restricts each element to one metric branch.
_EXCLUSIVE_METRICS_VERSIONS = frozenset({"1.4"})


class ContractAppliedError(ValueError):
    """Raised when a ContractApplied JSON value is structurally invalid."""


@dataclass(frozen=True)
class ContractAppliedElement:
    contract_id: str
    contract_commitment_id: str
    applied_cost: str | None = None
    applied_quantity: str | None = None
    applied_unit: str | None = None
    custom: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ContractApplied:
    elements: tuple[ContractAppliedElement, ...]
    custom: dict[str, object] = field(default_factory=dict)


class _DuplicateKey(Exception):
    def __init__(self, key: str) -> None:
        self.key = key


def _no_dup(pairs: list[tuple[str, object]]) -> dict:
    seen: dict = {}
    for key, value in pairs:
        if key in seen:
            raise _DuplicateKey(key)
        seen[key] = value
    return seen


def _require_str(value: object, key: str) -> str:
    if not isinstance(value, str) or isinstance(value, JsonNumber) or not value:
        raise ContractAppliedError(f"{key} must be a non-empty string")
    return value


def _numeric_text(value: object, key: str) -> str:
    """Return the exact numeric text of a JSON **number** token; reject anything else."""
    if isinstance(value, JsonNumber):
        return str(value)
    if isinstance(value, str):
        raise ContractAppliedError(f"{key} must be a JSON number, not a quoted string")
    raise ContractAppliedError(f"{key} must be a JSON number, got {value!r}")


def _resolve_id(
    obj: dict, accepted: tuple[str, ...], index: int, legacy_sink: set[str] | None
) -> str:
    """Return the identifier under one of ``accepted`` casings (canonical first).

    Both casings in one element is ambiguous and rejected; a legacy (non-canonical)
    casing is accepted and recorded in ``legacy_sink`` so callers can surface the
    normalization.
    """
    present = [key for key in accepted if key in obj]
    if len(present) > 1:
        raise ContractAppliedError(
            f"Elements[{index}] carries both {present[0]!r} and {present[1]!r}; "
            "use only the canonical casing"
        )
    key = present[0] if present else accepted[0]
    if present and key != accepted[0] and legacy_sink is not None:
        legacy_sink.add(key)
    return _require_str(obj.get(key), key)


def _parse_element(
    obj: object,
    ids: dict[str, tuple[str, ...]],
    index: int,
    *,
    exclusive_metrics: bool,
    legacy_sink: set[str] | None,
) -> ContractAppliedElement:
    if not isinstance(obj, dict):
        raise ContractAppliedError(f"Elements[{index}] must be an object")
    focus_keys = {*ids["contract"], *ids["commitment"], *_METRICS}
    for key in obj:
        if key not in focus_keys and not key.startswith("x_"):
            raise ContractAppliedError(
                f"Elements[{index}] custom key {key!r} must be prefixed with 'x_'"
            )
    contract_id = _resolve_id(obj, ids["contract"], index, legacy_sink)
    commitment_id = _resolve_id(obj, ids["commitment"], index, legacy_sink)
    cost = obj.get(_METRIC_COST)
    qty = obj.get(_METRIC_QTY)
    unit = obj.get(_METRIC_UNIT)
    if cost is None and qty is None:
        raise ContractAppliedError(
            f"Elements[{index}] must provide {_METRIC_COST} or {_METRIC_QTY}"
        )
    if qty is not None and unit is None:
        raise ContractAppliedError(
            f"Elements[{index}] must provide {_METRIC_UNIT} when {_METRIC_QTY} is present"
        )
    if exclusive_metrics and cost is not None and qty is not None:
        raise ContractAppliedError(
            f"Elements[{index}] must not provide both {_METRIC_COST} and {_METRIC_QTY} "
            "(FOCUS 1.4 ContractAppliedObjectSchema oneOf)"
        )
    return ContractAppliedElement(
        contract_id=contract_id,
        contract_commitment_id=commitment_id,
        applied_cost=_numeric_text(cost, _METRIC_COST) if cost is not None else None,
        applied_quantity=_numeric_text(qty, _METRIC_QTY) if qty is not None else None,
        applied_unit=_require_str(unit, _METRIC_UNIT) if unit is not None else None,
        custom={k: v for k, v in obj.items() if k.startswith("x_")},
    )


def parse(
    text: str, *, version: str = "1.4", legacy_sink: set[str] | None = None
) -> ContractApplied:
    """Parse and validate a ContractApplied JSON string for ``version``.

    ``legacy_sink`` (optional) collects the legacy pre-erratum identifier casings
    (``ContractID`` / ``ContractCommitmentID``) encountered in a 1.3 value, so the
    caller can report the normalization; parsing itself accepts them.
    """
    if version not in _ID_KEYS:
        raise ContractAppliedError(f"unsupported ContractApplied version {version!r}")
    try:
        obj = json.loads(
            text, object_pairs_hook=_no_dup, parse_float=JsonNumber, parse_int=JsonNumber
        )
    except _DuplicateKey as exc:
        raise ContractAppliedError(f"duplicate JSON key {exc.key!r}") from None
    except json.JSONDecodeError as exc:
        raise ContractAppliedError(f"invalid JSON: {exc.msg}") from None
    if not isinstance(obj, dict):
        raise ContractAppliedError("ContractApplied must be a JSON object")
    for key in obj:
        if key != "Elements" and not key.startswith("x_"):
            raise ContractAppliedError(f"top-level custom key {key!r} must be prefixed with 'x_'")
    elements = obj.get("Elements")
    if not isinstance(elements, list):
        raise ContractAppliedError("ContractApplied must have an 'Elements' array")
    if not elements:
        raise ContractAppliedError("'Elements' array must not be empty")
    ids = _ID_KEYS[version]
    exclusive = version in _EXCLUSIVE_METRICS_VERSIONS
    return ContractApplied(
        elements=tuple(
            _parse_element(e, ids, i, exclusive_metrics=exclusive, legacy_sink=legacy_sink)
            for i, e in enumerate(elements)
        ),
        custom={k: v for k, v in obj.items() if k.startswith("x_")},
    )


def to_json(ca: ContractApplied, *, version: str = "1.4") -> str:
    """Serialize ``ca`` to a compact ContractApplied JSON string for ``version``.

    Numeric metric values are emitted as JSON numbers (not quoted strings). For 1.4
    an element carrying both metric branches is refused (oneOf exclusivity).
    """
    if version not in _ID_KEYS:
        raise ContractAppliedError(f"unsupported ContractApplied version {version!r}")
    ids = _ID_KEYS[version]
    exclusive = version in _EXCLUSIVE_METRICS_VERSIONS
    elements: list[dict] = []
    for i, el in enumerate(ca.elements):
        if exclusive and el.applied_cost is not None and el.applied_quantity is not None:
            raise ContractAppliedError(
                f"Elements[{i}] carries both metric branches; FOCUS {version} allows only "
                "one (ContractAppliedObjectSchema oneOf)"
            )
        obj: dict[str, object] = {
            ids["contract"][0]: el.contract_id,
            ids["commitment"][0]: el.contract_commitment_id,
        }
        if el.applied_cost is not None:
            obj[_METRIC_COST] = el.applied_cost
        if el.applied_quantity is not None:
            obj[_METRIC_QTY] = el.applied_quantity
        if el.applied_unit is not None:
            obj[_METRIC_UNIT] = el.applied_unit
        obj.update(el.custom)
        elements.append(obj)
    top: dict[str, object] = {"Elements": elements}
    top.update(ca.custom)
    return dumps_object(top, numeric_keys=_NUMERIC_METRIC_KEYS)


def _to_1_4_exclusive(el: ContractAppliedElement) -> ContractAppliedElement:
    """Reduce a 1.3 element to one 1.4 metric branch (see the module docstring).

    When both branches are populated, the cost branch is kept and quantity/unit are
    preserved losslessly as ``x_``-prefixed custom keys.
    """
    if el.applied_cost is None or el.applied_quantity is None:
        return el
    custom = dict(el.custom)
    custom[f"x_{_METRIC_QTY}"] = JsonNumber(el.applied_quantity)
    if el.applied_unit is not None:
        custom[f"x_{_METRIC_UNIT}"] = el.applied_unit
    return ContractAppliedElement(
        contract_id=el.contract_id,
        contract_commitment_id=el.contract_commitment_id,
        applied_cost=el.applied_cost,
        custom=custom,
    )


def migrate_1_3_to_1_4(text: str, *, legacy_sink: set[str] | None = None) -> str:
    """Migrate a FOCUS 1.3 ContractApplied JSON string to the 1.4 schema.

    Normalizes any legacy pre-erratum identifier casing (recorded in ``legacy_sink``
    when provided) and enforces the 1.4 metric exclusivity (both-branch 1.3 elements
    keep cost; quantity/unit move to ``x_`` custom keys, losslessly).
    """
    ca = parse(text, version="1.3", legacy_sink=legacy_sink)
    ca = ContractApplied(
        elements=tuple(_to_1_4_exclusive(el) for el in ca.elements),
        custom=ca.custom,
    )
    return to_json(ca, version="1.4")
