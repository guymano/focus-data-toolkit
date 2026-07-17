"""Release tooling: the SBOM generator and the release verifier (P2-E).

Marked ``packaging`` (each builds a distribution). Exercises the deterministic CycloneDX SBOM and
the offline release verifier against a freshly built dist, so the release pipeline's gates are
tested here rather than only running in the workflow.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.packaging

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

from generate_sbom import build_sbom  # noqa: E402
from verify_release import verify_release  # noqa: E402


def _version() -> str:
    ns: dict[str, str] = {}
    exec((_ROOT / "src" / "focus_data_toolkit" / "_version.py").read_text(), ns)  # noqa: S102
    return ns["__version__"]


@pytest.fixture(scope="module")
def release_dir(tmp_path_factory) -> Path:
    """Build wheel+sdist, generate the SBOM and SHA256SUMS — a full release directory."""
    out = tmp_path_factory.mktemp("release")
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(out), str(_ROOT)],
        check=True,
        capture_output=True,
    )
    wheel = next(out.glob("*.whl"))
    sbom = build_sbom(wheel, source_date_epoch=1700000000)
    (out / "sbom.cdx.json").write_text(json.dumps(sbom, indent=2, sort_keys=True) + "\n")
    lines = []
    for artifact in sorted(out.iterdir()):
        if artifact.name == "SHA256SUMS":
            continue
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        lines.append(f"{digest}  {artifact.name}")
    (out / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    return out


def test_sbom_structure_and_focus_model(release_dir):
    doc = json.loads((release_dir / "sbom.cdx.json").read_text())
    assert doc["bomFormat"] == "CycloneDX"
    assert doc["specVersion"] == "1.5"
    assert doc["metadata"]["component"]["version"] == _version()
    model = next(c for c in doc["components"] if c["bom-ref"] == "focus-1.4-data-model")
    assert model["type"] == "data"
    assert model["licenses"][0]["license"]["id"] == "CC-BY-4.0"
    # The model hash in the SBOM matches the committed model provenance.
    prov = json.loads(
        (_ROOT / "src" / "focus_data_toolkit" / "model" / "model_provenance.json").read_text()
    )
    assert model["hashes"][0]["content"] == prov["output"]["sha256"]
    # Dev/release tooling is not in the distributed-package SBOM.
    names = {c["name"] for c in doc["components"]}
    assert not ({"pytest", "mypy", "ruff", "build", "twine"} & names)


def test_sbom_is_deterministic(release_dir):
    wheel = next(release_dir.glob("*.whl"))
    a = build_sbom(wheel, source_date_epoch=1700000000)
    b = build_sbom(wheel, source_date_epoch=1700000000)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_verify_release_passes_on_wellformed_dist(release_dir):
    assert verify_release(release_dir) == []


def test_verify_release_detects_tampered_checksum(release_dir, tmp_path):
    # Copy the release dir, corrupt the first recorded hash, and confirm the verifier catches it.
    import shutil

    corrupt = tmp_path / "corrupt"
    shutil.copytree(release_dir, corrupt)
    sums = corrupt / "SHA256SUMS"
    lines = sums.read_text().splitlines()
    digest, name = lines[0].split(None, 1)
    flipped = ("f" if digest[0] != "f" else "0") + digest[1:]  # change one hex digit
    lines[0] = f"{flipped}  {name}"
    sums.write_text("\n".join(lines) + "\n")
    errors = verify_release(corrupt)
    assert any("SHA256SUMS mismatch" in e for e in errors), errors
