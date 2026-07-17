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
# GitHub loads both extensions, and the pin checker scans both — the guards must too.
_WORKFLOW_FILES = sorted([*_WORKFLOWS.glob("*.yml"), *_WORKFLOWS.glob("*.yaml")])

sys.path.insert(0, str(_ROOT / "scripts"))
from check_pinned_actions import unpinned  # noqa: E402


def test_all_actions_pinned_to_sha():
    bad = unpinned(_WORKFLOWS)
    assert bad == [], "unpinned actions: " + ", ".join(f"{p.name}:{n} {ref}" for p, n, ref in bad)


@pytest.mark.parametrize("workflow", _WORKFLOW_FILES, ids=lambda p: p.name)
def test_workflow_declares_top_level_permissions(workflow):
    # A top-level `permissions:` block (column 0) is required so the default GITHUB_TOKEN is
    # least-privilege; jobs elevate explicitly where needed.
    text = workflow.read_text()
    assert "\npermissions:" in text, f"{workflow.name} must declare top-level permissions"


def test_docker_actions_must_be_digest_pinned(tmp_path):
    # A floating docker:// tag must be rejected; an @sha256 digest must pass. (Regression guard so
    # the SHA-pin policy also covers container actions, not just `owner/repo@<sha>` references.)
    wf = tmp_path / "wf.yml"
    digest = "sha256:" + "a" * 64
    wf.write_text(
        "jobs:\n"
        "  a:\n"
        "    steps:\n"
        "      - uses: docker://alpine:latest\n"
        f"      - uses: docker://ghcr.io/acme/tool@{digest}\n"
    )
    bad = unpinned(tmp_path)
    flagged = {ref for _, _, ref in bad}
    assert "docker://alpine:latest" in flagged
    assert f"docker://ghcr.io/acme/tool@{digest}" not in flagged
