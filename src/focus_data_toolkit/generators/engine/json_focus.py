"""Single-source FOCUS JSON builders for the generators.

Both the Split Cost Allocation ``AllocatedMethodDetails`` object and the
``ContractApplied`` object are FOCUS JSON with *Numeric* properties that MUST be emitted
as JSON numbers (not quoted strings). They are built here, once, on top of
:func:`focus_data_toolkit.focus_json.dumps_object`, so no generator, provider or scenario
re-implements the rule. Previously the SCA object was built two divergent ways (the 1.3
generators via ``dumps_object``; ``scenarios.py`` by string concatenation) — both now route
through :func:`allocated_method_details`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from focus_data_toolkit.focus_json import dumps_object

# AllocatedMethodDetails.Elements[*] numeric properties (FOCUS 1.3 Split Cost Allocation).
SCA_NUMERIC_KEYS: frozenset[str] = frozenset({"AllocatedRatio", "UsageQuantity"})
# ContractApplied.Elements[*] numeric properties (FOCUS 1.3).
CONTRACT_APPLIED_NUMERIC_KEYS: frozenset[str] = frozenset(
    {"ContractCommitmentAppliedCost", "ContractCommitmentAppliedQuantity"}
)


def allocated_method_details(
    elements: Sequence[Mapping[str, object]],
    *,
    numeric_keys: frozenset[str] = SCA_NUMERIC_KEYS,
) -> str:
    """Serialise a FOCUS ``AllocatedMethodDetails`` object from its ``Elements``.

    Each element's numeric properties (``AllocatedRatio`` / ``UsageQuantity`` by default)
    are emitted as JSON numbers; their string values must be JSON number literals or
    ``dumps_object`` raises (a stricter, safer contract than string concatenation).
    """
    return dumps_object({"Elements": list(elements)}, numeric_keys=numeric_keys)


def contract_applied(
    commit_id: str,
    contract_id: str,
    applied_cost: str,
    applied_qty: str,
    applied_unit: str,
) -> str:
    """FOCUS 1.3 ``ContractApplied`` JSON: a top-level ``Elements`` array linking a Cost
    and Usage row to the Contract Commitment dataset via ``ContractCommitmentID``. The
    applied cost/quantity are emitted as JSON numbers."""
    return dumps_object(
        {
            "Elements": [
                {
                    "ContractID": contract_id,
                    "ContractCommitmentID": commit_id,
                    "ContractCommitmentAppliedCost": applied_cost,
                    "ContractCommitmentAppliedQuantity": applied_qty,
                    "ContractCommitmentAppliedUnit": applied_unit,
                }
            ]
        },
        numeric_keys=CONTRACT_APPLIED_NUMERIC_KEYS,
    )
