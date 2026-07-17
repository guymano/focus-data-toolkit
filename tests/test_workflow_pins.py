"""CI/supply-chain guardrails: workflows are SHA-pinned and least-privilege.

These run in the default suite (fast, no network) so a workflow that adds an unpinned action or
drops its ``permissions`` block fails locally before it reaches CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"

sys.path.insert(0, str(_ROOT / "scripts"))
from check_pinned_actions import unpinned  # noqa: E402


def test_all_actions_pinned_to_sha():
    bad = unpinned(_WORKFLOWS)
    assert bad == [], "unpinned actions: " + ", ".join(f"{p.name}:{n} {ref}" for p, n, ref in bad)


@pytest.mark.parametrize("workflow", sorted(_WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_workflow_declares_top_level_permissions(workflow):
    # A top-level `permissions:` block (column 0) is required so the default GITHUB_TOKEN is
    # least-privilege; jobs elevate explicitly where needed.
    text = workflow.read_text()
    assert "\npermissions:" in text, f"{workflow.name} must declare top-level permissions"
