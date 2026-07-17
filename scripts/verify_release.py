#!/usr/bin/env python3
"""Verify a built focus-data-toolkit release directory before (or after) publishing.

Standard-library only. Offline, deterministic checks over a ``dist/`` directory produced by the
release pipeline:

* ``SHA256SUMS`` exists and every listed file matches its recorded hash;
* the wheel, the sdist and the SBOM are all present and checksummed;
* the CycloneDX SBOM is well-formed and names the package + the FOCUS model data component;
* the version agrees across the wheel filename, the sdist filename, the SBOM, the committed
  ``_version.py``, and a **dated** ``CHANGELOG.md`` entry (not ``[Unreleased]``).

It also prints the ``gh attestation verify`` commands to run against the *published* artifacts
(those require network + the GitHub CLI and are intentionally not run here).

    python scripts/verify_release.py --dist dist
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_VERSION_FILE = _REPO / "src" / "focus_data_toolkit" / "_version.py"
_CHANGELOG = _REPO / "CHANGELOG.md"

_WHEEL_RE = re.compile(r"^focus_data_toolkit-(?P<v>[^-]+)-py3-none-any\.whl$")
_SDIST_RE = re.compile(r"^focus_data_toolkit-(?P<v>.+)\.tar\.gz$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _committed_version() -> str:
    ns: dict = {}
    exec(_VERSION_FILE.read_text(encoding="utf-8"), ns)  # noqa: S102 - trusted repo file
    return ns["__version__"]


def _changelog_has_dated(version: str) -> bool:
    pat = re.compile(
        r"^##\s*\[" + re.escape(version) + r"\][^\n]*\d{4}-\d{2}-\d{2}", re.MULTILINE
    )
    return bool(pat.search(_CHANGELOG.read_text(encoding="utf-8")))


def _verify_checksums(dist: Path, errors: list[str]) -> set[str]:
    """Verify SHA256SUMS; return the set of filenames it covers."""
    sums = dist / "SHA256SUMS"
    if not sums.is_file():
        errors.append("SHA256SUMS is missing")
        return set()
    covered: set[str] = set()
    for line in sums.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            digest, name = line.split(None, 1)
        except ValueError:
            errors.append(f"SHA256SUMS: malformed line: {line!r}")
            continue
        name = name.lstrip("*").strip()
        if name.startswith("./"):
            name = name[2:]  # `sha256sum ./*.whl` records a leading ./
        target = dist / name
        if not target.is_file():
            errors.append(f"SHA256SUMS lists a missing file: {name}")
            continue
        if _sha256(target) != digest:
            errors.append(f"SHA256SUMS mismatch for {name}")
        covered.add(name)
    return covered


def _verify_sbom(dist: Path, version: str, errors: list[str]) -> None:
    sbom = dist / "sbom.cdx.json"
    if not sbom.is_file():
        errors.append("SBOM (sbom.cdx.json) is missing")
        return
    try:
        doc = json.loads(sbom.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"SBOM is not valid JSON: {exc}")
        return
    if doc.get("bomFormat") != "CycloneDX":
        errors.append("SBOM bomFormat is not 'CycloneDX'")
    if not str(doc.get("specVersion", "")).startswith("1."):
        errors.append(f"SBOM specVersion unexpected: {doc.get('specVersion')!r}")
    comp = doc.get("metadata", {}).get("component", {})
    if comp.get("version") != version:
        errors.append(f"SBOM component version {comp.get('version')!r} != release {version!r}")
    refs = {c.get("bom-ref") for c in doc.get("components", [])}
    if "focus-1.4-data-model" not in refs:
        errors.append("SBOM does not include the FOCUS model data component")


def verify_release(dist: Path) -> list[str]:
    errors: list[str] = []
    if not dist.is_dir():
        return [f"dist directory not found: {dist}"]

    wheels = [p for p in dist.iterdir() if _WHEEL_RE.match(p.name)]
    sdists = [p for p in dist.iterdir() if _SDIST_RE.match(p.name)]
    if len(wheels) != 1:
        errors.append(f"expected exactly one wheel, found {len(wheels)}")
    if len(sdists) != 1:
        errors.append(f"expected exactly one sdist, found {len(sdists)}")
    if errors:
        return errors

    wheel_v = _WHEEL_RE.match(wheels[0].name)["v"]  # type: ignore[index]
    sdist_v = _SDIST_RE.match(sdists[0].name)["v"]  # type: ignore[index]
    committed = _committed_version()

    if wheel_v != sdist_v:
        errors.append(f"wheel version {wheel_v} != sdist version {sdist_v}")
    if wheel_v != committed:
        errors.append(f"wheel version {wheel_v} != _version.py {committed}")
    if not _changelog_has_dated(committed):
        errors.append(f"CHANGELOG.md has no dated '[{committed}]' entry (still Unreleased?)")

    covered = _verify_checksums(dist, errors)
    for artifact in (wheels[0].name, sdists[0].name, "sbom.cdx.json"):
        if artifact not in covered:
            errors.append(f"{artifact} is not listed in SHA256SUMS")

    _verify_sbom(dist, committed, errors)
    return errors


def _print_attestation_hints(dist: Path) -> None:
    print("\nTo verify the *published* artifacts' build provenance (needs network + gh):")
    print("  gh attestation verify <artifact> --repo guymano/focus-data-toolkit")
    for pattern in ("*.whl", "*.tar.gz", "sbom.cdx.json"):
        for p in sorted(dist.glob(pattern)):
            print(f"  gh attestation verify {p.name} --repo guymano/focus-data-toolkit")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify a built release directory.")
    ap.add_argument("--dist", type=Path, default=_REPO / "dist", help="release directory")
    ap.add_argument("--no-hints", action="store_true", help="do not print attestation hints")
    args = ap.parse_args(argv)

    errors = verify_release(args.dist)
    if errors:
        print("Release verification FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1
    print(f"Release verified OK: {args.dist}")
    if not args.no_hints:
        _print_attestation_hints(args.dist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
