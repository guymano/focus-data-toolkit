"""Each FOCUS rule is implemented in exactly one place (P2-A de-duplication goal).

Proven by API/architecture — callable identity and import wiring — rather than fragile source
regexes: if a rule were re-implemented, the identity assertions below would fail. A light
structural check backs this up without pinning internal layout.
"""

from __future__ import annotations

import ast
from pathlib import Path

from focus_data_toolkit.generators import scenarios
from focus_data_toolkit.generators.engine import determinism, json_focus, scenarios_core
from focus_data_toolkit.generators.versions import v1_3

_GENERATORS_DIR = Path(scenarios.__file__).parent


def test_sca_json_built_in_one_place():
    # The generators and the coherent scenarios share the same builder object.
    assert scenarios_core.allocated_method_details is json_focus.allocated_method_details
    assert scenarios.allocated_method_details is json_focus.allocated_method_details


def test_contract_applied_built_in_one_place():
    assert v1_3.contract_applied is json_focus.contract_applied


def test_rounding_defined_in_one_place():
    # ROUND_HALF_UP quantisation lives only in determinism.q, reused everywhere.
    assert scenarios_core.q is determinism.q


def test_sca_reconciliation_is_equivalent():
    # A scenario SCA element and a generator SCA element serialise via the same path.
    element = {"AllocatedRatio": "0.500000", "UsageUnit": "Hours", "UsageQuantity": "1"}
    assert json_focus.allocated_method_details([element]) == (
        '{"Elements":[{"AllocatedRatio":0.500000,"UsageUnit":"Hours","UsageQuantity":1}]}'
    )


def _imports_dumps_object(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("focus_json"):
            return any(alias.name == "dumps_object" for alias in node.names)
    return False


def test_dumps_object_imported_only_by_json_focus():
    # The raw FOCUS-JSON serializer is wrapped once; provider/version/scenario modules must go
    # through engine.json_focus instead of calling dumps_object directly.
    offenders = [
        p.relative_to(_GENERATORS_DIR).as_posix()
        for p in _GENERATORS_DIR.rglob("*.py")
        if p.name != "json_focus.py" and _imports_dumps_object(p)
    ]
    assert offenders == [], f"dumps_object imported outside json_focus: {offenders}"
