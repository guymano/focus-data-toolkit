#!/usr/bin/env python3
"""Generate a deterministic *resolved* CycloneDX 1.5 SBOM from ``uv.lock``.

Standard-library only. Complements ``generate_sbom.py`` (which records the *declared*
dependency ranges from the wheel's METADATA): this profile records what those ranges
**resolve to** — exact versions, versioned purls, the sha256 of every distributable artifact
(sdist + wheels, as hashed ``distribution`` external references), environment markers, the
extra each package enters through, and the transitive dependency tree — all read from the
committed ``uv.lock``, so the SBOM describes the same resolution the lockfile pins.

Only the *runtime* extras are described (``parquet``, ``validator``, ``all``); ``dev`` /
``release`` tooling is excluded, exactly as in the declared SBOM. Licenses are filled from
installed package metadata when available in the generating environment and omitted
otherwise (never guessed).

Determinism: components and references are sorted, ``metadata.timestamp`` derives from
``SOURCE_DATE_EPOCH`` (omitted otherwise), and JSON is written with sorted keys — the same
(wheel, uv.lock, epoch) yields byte-identical output.

    python scripts/generate_resolved_sbom.py dist/focus_data_toolkit-*.whl -o dist/sbom.resolved.cdx.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import email.parser
import importlib.metadata
import json
import os
import sys
import tomllib
import zipfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_LOCK = _REPO / "uv.lock"
_PACKAGE = "focus-data-toolkit"

# Extras that are build/dev tooling, not part of the distributed runtime (same set as the
# declared-deps SBOM in generate_sbom.py).
_TOOLING_EXTRAS = {"dev", "release"}


def _wheel_root(wheel: Path) -> tuple[str, str, str]:
    """(name, version, license expression) from the wheel's METADATA."""
    with zipfile.ZipFile(wheel) as zf:
        meta_name = next((n for n in zf.namelist() if n.endswith(".dist-info/METADATA")), None)
        if meta_name is None:
            raise SystemExit(f"no .dist-info/METADATA in {wheel}")
        meta = email.parser.Parser().parsestr(zf.read(meta_name).decode("utf-8"))
    return (
        meta.get("Name", _PACKAGE),
        meta.get("Version", "0"),
        meta.get("License-Expression") or "MIT",
    )


def _license_entry(name: str) -> dict | None:
    """CycloneDX license entry from *installed* metadata; None when not resolvable here."""
    try:
        meta = importlib.metadata.metadata(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    expression = meta.get("License-Expression")
    if expression:
        return {"expression": expression}
    license_field = meta.get("License")
    if license_field and license_field != "UNKNOWN" and "\n" not in license_field:
        return {"license": {"name": license_field}}
    for classifier in meta.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            return {"license": {"name": classifier.split("::")[-1].strip()}}
    return None


def _distribution_refs(entry: dict) -> list[dict]:
    """Hashed ``distribution`` external references for every sdist/wheel in the lock entry."""
    refs = []
    artifacts = list(entry.get("wheels", []))
    if entry.get("sdist"):
        artifacts.append(entry["sdist"])
    for artifact in artifacts:
        url = artifact.get("url")
        digest = (artifact.get("hash") or "").removeprefix("sha256:")
        if not url or not digest:
            continue
        refs.append(
            {
                "type": "distribution",
                "url": url,
                "hashes": [{"alg": "SHA-256", "content": digest}],
            }
        )
    return sorted(refs, key=lambda r: r["url"])


def _runtime_closure(lock: dict) -> tuple[dict[str, dict], dict[str, set[str]], dict[str, set[str]]]:
    """(lock entry, entered-via extras, markers) per package in the runtime-extras closure."""
    by_name = {p["name"]: p for p in lock["package"]}
    project = by_name[_PACKAGE]
    roots: list[tuple[str, str, str]] = [  # (name, extra, marker)
        (dep["name"], extra, dep.get("marker", ""))
        for extra, deps in (project.get("optional-dependencies") or {}).items()
        if extra not in _TOOLING_EXTRAS
        for dep in deps
    ]
    closure: dict[str, dict] = {}
    via: dict[str, set[str]] = {}
    markers: dict[str, set[str]] = {}
    queue = list(roots)
    while queue:
        name, extra, marker = queue.pop()
        via.setdefault(name, set()).add(extra)
        if marker:
            markers.setdefault(name, set()).add(marker)
        if name in closure:
            continue
        entry = by_name.get(name)
        if entry is None:  # platform-filtered out of the lock: nothing to describe
            continue
        closure[name] = entry
        for dep in entry.get("dependencies", []):
            queue.append((dep["name"], extra, dep.get("marker", "")))
    return closure, via, markers


def build_resolved_sbom(wheel: Path, source_date_epoch: int | None, lock_path: Path = _LOCK) -> dict:
    name, version, license_expr = _wheel_root(wheel)
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    closure, via, markers = _runtime_closure(lock)

    purl = f"pkg:pypi/{name.lower()}@{version}"
    root = {
        "type": "application",
        "bom-ref": purl,
        "name": name,
        "version": version,
        "purl": purl,
        "licenses": [{"license": {"id": license_expr}}],
    }

    components = []
    dependencies = []
    for pkg_name in sorted(closure):
        entry = closure[pkg_name]
        pkg_version = entry["version"]
        ref = f"pkg:pypi/{pkg_name}@{pkg_version}"
        component: dict = {
            "type": "library",
            "bom-ref": ref,
            "name": pkg_name,
            "version": pkg_version,
            "purl": ref,
            "scope": "optional",  # every runtime dependency is an optional extra
            "externalReferences": _distribution_refs(entry),
            "properties": [
                {"name": "focus:extras", "value": ",".join(sorted(via.get(pkg_name, ())))},
            ],
        }
        for marker in sorted(markers.get(pkg_name, ())):
            component["properties"].append({"name": "uv:marker", "value": marker})
        license_entry = _license_entry(pkg_name)
        if license_entry is not None:
            component["licenses"] = [license_entry]
        components.append(component)
        depends_on = sorted(
            f"pkg:pypi/{d['name']}@{closure[d['name']]['version']}"
            for d in entry.get("dependencies", [])
            if d["name"] in closure
        )
        dependencies.append({"ref": ref, "dependsOn": depends_on})

    # Direct dependencies = the packages the project's runtime extras name themselves.
    by_name = {p["name"]: p for p in lock["package"]}
    project = by_name[_PACKAGE]
    direct = sorted(
        {
            f"pkg:pypi/{d['name']}@{closure[d['name']]['version']}"
            for extra, deps in (project.get("optional-dependencies") or {}).items()
            if extra not in _TOOLING_EXTRAS
            for d in deps
            if d["name"] in closure
        }
    )

    metadata: dict = {
        "tools": [{"vendor": name, "name": "generate_resolved_sbom.py", "version": version}],
        "component": root,
        "properties": [
            {"name": "focus:profile", "value": "resolved"},
            {"name": "focus:resolution_source", "value": "uv.lock"},
        ],
    }
    if source_date_epoch is not None:
        ts = _dt.datetime.fromtimestamp(source_date_epoch, tz=_dt.UTC)
        metadata["timestamp"] = ts.strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": metadata,
        "components": components,
        "dependencies": [{"ref": purl, "dependsOn": direct}, *dependencies],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate a deterministic resolved-dependency CycloneDX SBOM from uv.lock."
    )
    ap.add_argument("wheel", type=Path, help="path to the built .whl (names the root component)")
    ap.add_argument("-o", "--output", type=Path, help="output path (default: stdout)")
    args = ap.parse_args(argv)

    sde = os.environ.get("SOURCE_DATE_EPOCH")
    sbom = build_resolved_sbom(args.wheel, int(sde) if sde else None)
    text = json.dumps(sbom, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
