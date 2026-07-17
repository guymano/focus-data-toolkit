#!/usr/bin/env python3
"""Fail if any GitHub Actions ``uses:`` reference is not pinned to a full 40-char commit SHA.

Project-specific supply-chain policy (complements actionlint/zizmor, which lint workflow syntax
and security posture). Local actions (``./...``) are allowed; Docker image actions
(``docker://...``) must be pinned to an immutable ``@sha256:<digest>``, not a floating tag.
"""

from __future__ import annotations

import re
from pathlib import Path

_SHA = re.compile(r"^[0-9a-f]{40}$")
_DOCKER_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
_USES = re.compile(r"""^\s*(?:-\s*)?uses:\s*(?P<ref>[^\s#]+)""")


def unpinned(workflows_dir: Path) -> list[tuple[Path, int, str]]:
    """Return (file, line_no, ref) for every ``uses:`` not immutably pinned.

    A reference is pinned when it is a local action (``./...``), a Docker image at an explicit
    ``@sha256:<digest>``, or an action at a full 40-char commit SHA. Floating tags/branches — for
    both regular actions and ``docker://`` images — are reported.
    """
    bad: list[tuple[Path, int, str]] = []
    for wf in sorted([*workflows_dir.rglob("*.yml"), *workflows_dir.rglob("*.yaml")]):
        for line_no, line in enumerate(wf.read_text().splitlines(), start=1):
            match = _USES.match(line)
            if not match:
                continue
            ref = match.group("ref").strip().strip("'\"")
            if ref.startswith("./"):
                continue  # local composite action
            if ref.startswith("docker://"):
                if not _DOCKER_DIGEST.search(ref):
                    bad.append((wf, line_no, ref))  # docker image must be @sha256:<digest>
                continue
            _, sep, at = ref.partition("@")
            if not sep or not _SHA.match(at):
                bad.append((wf, line_no, ref))
    return bad


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        print(f"no workflows directory at {workflows_dir}")
        return 0
    bad = unpinned(workflows_dir)
    if bad:
        print("Unpinned GitHub Actions (must be `owner/repo@<40-hex-sha>` or `docker://…@sha256:…`):")
        for wf, line_no, ref in bad:
            print(f"  {wf.relative_to(root)}:{line_no}: {ref}")
        return 1
    print("All GitHub Actions are pinned to a full commit SHA.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
