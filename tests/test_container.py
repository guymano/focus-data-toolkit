"""Static checks on the Runner Dockerfile (Lot B).

These need no Docker daemon (the full build + `docker run` behaviour is exercised by the
`container.yml` workflow). They guard the invariants the container-security posture depends on
— and specifically the base-image digest pin, which ``scripts/check_pinned_actions.py`` does
not cover (it only scans ``.github/workflows``).
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"


def _dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def test_dockerfile_exists():
    assert DOCKERFILE.is_file()


def test_base_image_is_digest_pinned():
    text = _dockerfile()
    arg_base = [ln for ln in text.splitlines() if ln.strip().startswith("ARG BASE=")]
    assert arg_base, "expected a single `ARG BASE=...` declaration"
    assert all("@sha256:" in ln for ln in arg_base), arg_base
    from_lines = [ln for ln in text.splitlines() if ln.strip().startswith("FROM ")]
    assert from_lines, "no FROM lines"
    for line in from_lines:
        # Either an inline digest or a reference to the digest-pinned ARG — never a floating tag.
        assert "@sha256:" in line or "${BASE}" in line, line


def test_runs_as_non_root():
    users = [ln.strip() for ln in _dockerfile().splitlines() if ln.strip().startswith("USER ")]
    assert users, "no USER directive — the image would run as root"
    assert users[-1].split()[1] != "root", users[-1]


def test_entrypoint_is_exec_form_focus_toolkit():
    # Exec form so PID 1 receives SIGTERM (clean cancel); the entrypoint is the CLI itself.
    assert 'ENTRYPOINT ["focus-toolkit"]' in _dockerfile()


def test_work_dir_configured_and_volumes_declared():
    text = _dockerfile()
    assert "FOCUS_TOOLKIT_WORK_DIR=/work" in text
    assert '/input' in text and '/output' in text and '/work' in text
