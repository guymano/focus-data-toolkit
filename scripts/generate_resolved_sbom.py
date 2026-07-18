#!/usr/bin/env python3
"""Generate a deterministic *resolved* CycloneDX 1.5 SBOM from ``uv.lock``.

Standard-library only. Complements ``generate_sbom.py`` (which records the *declared*
dependency ranges from the wheel's METADATA): this profile records what those ranges
**resolve to** — exact versions, versioned purls, the sha256 of every distributable artifact
(sdist + wheels, as hashed ``distribution`` external references), every extra a package is
reachable from (propagated over all paths, never only the first one visited), the
environment markers on the project's direct extra requirements (labelled per extra), and
the transitive dependency tree — all read from the committed ``uv.lock``, so the SBOM
describes the same resolution the lockfile pins.

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


def _runtime_extras(lock: dict) -> dict[str, list[dict]]:
    project = next(p for p in lock["package"] if p["name"] == _PACKAGE)
    return {
        extra: deps
        for extra, deps in (project.get("optional-dependencies") or {}).items()
        if extra not in _TOOLING_EXTRAS
    }


def _runtime_closure(
    lock: dict,
) -> tuple[dict[str, dict], dict[str, set[str]], dict[str, set[tuple[str, str]]]]:
    """(lock entry, reachable-from extras, direct extra markers) per closure package.

    Extras are propagated to a **fixpoint over every edge** — a package reachable from
    several extras (directly or transitively) records all of them, never only the path
    visited first. Markers are recorded only from the project's *direct* extra
    requirements, labelled with their extra: aggregating transitive edge markers onto a
    package would misstate it as conditional even when another path installs it
    unconditionally (e.g. pyarrow is unconditional via ``parquet`` but gated by
    ``python >= 3.12`` via ``validator``).
    """
    by_name = {p["name"]: p for p in lock["package"]}
    runtime_extras = _runtime_extras(lock)

    closure: dict[str, dict] = {}
    queue = [dep["name"] for deps in runtime_extras.values() for dep in deps]
    while queue:
        name = queue.pop()
        if name in closure:
            continue
        entry = by_name.get(name)
        if entry is None:  # platform-filtered out of the lock: nothing to describe
            continue
        closure[name] = entry
        queue.extend(dep["name"] for dep in entry.get("dependencies", []))

    extras_of: dict[str, set[str]] = {}
    for extra, deps in runtime_extras.items():
        for dep in deps:
            if dep["name"] in closure:
                extras_of.setdefault(dep["name"], set()).add(extra)
    changed = True
    while changed:
        changed = False
        for parent, entry in closure.items():
            for dep in entry.get("dependencies", []):
                child = dep["name"]
                if child not in closure:
                    continue
                missing = extras_of.get(parent, set()) - extras_of.get(child, set())
                if missing:
                    extras_of.setdefault(child, set()).update(missing)
                    changed = True

    direct_markers: dict[str, set[tuple[str, str]]] = {}
    for extra, deps in runtime_extras.items():
        for dep in deps:
            if dep.get("marker") and dep["name"] in closure:
                direct_markers.setdefault(dep["name"], set()).add((extra, dep["marker"]))
    return closure, extras_of, direct_markers


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
        # Compound SPDX expressions use the expression form (see generate_sbom.py).
        "licenses": (
            [{"expression": license_expr}] if " " in license_expr
            else [{"license": {"id": license_expr}}]
        ),
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
        for extra, marker in sorted(markers.get(pkg_name, ())):
            component["properties"].append(
                {"name": "uv:marker", "value": f"via {extra}: {marker}"}
            )
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
    direct = sorted(
        {
            f"pkg:pypi/{d['name']}@{closure[d['name']]['version']}"
            for deps in _runtime_extras(lock).values()
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
