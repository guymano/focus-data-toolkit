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

from generate_resolved_sbom import build_resolved_sbom  # noqa: E402
from generate_sbom import build_sbom  # noqa: E402
from verify_release import verify_release  # noqa: E402


def _version() -> str:
    ns: dict[str, str] = {}
    exec((_ROOT / "src" / "focus_data_toolkit" / "_version.py").read_text(), ns)  # noqa: S102
    return ns["__version__"]


@pytest.fixture(scope="module")
def release_dir(tmp_path_factory) -> Path:
    """Build the full release asset set the pipeline produces, into one directory."""
    out = tmp_path_factory.mktemp("release")
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(out), str(_ROOT)],
        check=True,
        capture_output=True,
    )
    wheel = next(out.glob("*.whl"))
    sbom = build_sbom(wheel, source_date_epoch=1700000000)
    (out / "sbom.cdx.json").write_text(json.dumps(sbom, indent=2, sort_keys=True) + "\n")
    resolved = build_resolved_sbom(wheel, source_date_epoch=1700000000)
    (out / "sbom.resolved.cdx.json").write_text(
        json.dumps(resolved, indent=2, sort_keys=True) + "\n"
    )
    (out / "release-manifest.json").write_text('{"test": true}\n')
    model_dir = _ROOT / "src" / "focus_data_toolkit"
    for src in (
        model_dir / "model" / "model_provenance.json",
        model_dir / "model" / "json_schemas" / "json_schemas_provenance.json",
        model_dir / "supplement" / "adapters" / "adapters_provenance.json",
    ):
        (out / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
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


def test_resolved_sbom_pins_exact_versions_with_hashed_distributions(release_dir):
    doc = json.loads((release_dir / "sbom.resolved.cdx.json").read_text(encoding="utf-8"))
    assert doc["bomFormat"] == "CycloneDX"
    assert doc["metadata"]["component"]["version"] == _version()
    comps = {c["name"]: c for c in doc["components"]}
    # Runtime extras resolve to exact versions (never ranges) ...
    pyarrow = comps["pyarrow"]
    assert pyarrow["purl"] == f"pkg:pypi/pyarrow@{pyarrow['version']}"
    assert not any(ch in pyarrow["version"] for ch in "<>=~,")
    # ... with a sha256-hashed distribution reference per published artifact ...
    refs = pyarrow["externalReferences"]
    assert refs and all(
        r["type"] == "distribution" and r["hashes"][0]["alg"] == "SHA-256" for r in refs
    )
    # ... and the extra each package enters through.
    extras_prop = next(p for p in pyarrow["properties"] if p["name"] == "focus:extras")
    assert "parquet" in extras_prop["value"]
    # The transitive tree is present: focus-validator's own deps are components too.
    assert "focus-validator" in comps
    validator_deps = next(
        d for d in doc["dependencies"] if d["ref"] == comps["focus-validator"]["bom-ref"]
    )
    assert validator_deps["dependsOn"], "transitive dependencies missing"
    # Dev/release tooling never leaks into the runtime SBOM.
    assert "pytest" not in comps and "ruff" not in comps and "twine" not in comps


def test_resolved_sbom_is_deterministic(release_dir):
    wheel = next(release_dir.glob("*.whl"))
    a = build_resolved_sbom(wheel, source_date_epoch=1700000000)
    b = build_resolved_sbom(wheel, source_date_epoch=1700000000)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_build_backend_lock_is_hash_pinned():
    lock = (_ROOT / "constraints" / "build-backend.txt").read_text(encoding="utf-8")
    assert "setuptools==" in lock, "the release build backend must be pinned exactly"
    assert "--hash=sha256:" in lock, "the backend pin must carry artifact hashes"
