#!/usr/bin/env python3
"""Generate a deterministic CycloneDX 1.5 SBOM for a built focus-data-toolkit wheel.

Standard-library only. The SBOM describes the *distributed package*: the application component, the
embedded **FOCUS 1.4 data model** as a first-class ``data`` component (with its CC-BY-4.0 license and
provenance hash), and each declared (optional-extra) dependency parsed from the wheel's METADATA.

Determinism: no wall-clock and no random serial number. ``metadata.timestamp`` is derived from
``SOURCE_DATE_EPOCH`` when set (and omitted otherwise), components are sorted, and JSON is written
with sorted keys — so the same wheel + epoch yields byte-identical SBOM output.

    python scripts/generate_sbom.py dist/focus_data_toolkit-0.9.0-py3-none-any.whl -o dist/sbom.cdx.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import email.parser
import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PROVENANCE = _REPO / "src" / "focus_data_toolkit" / "model" / "model_provenance.json"

_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(.*)$")
_EXTRA_RE = re.compile(r'extra\s*==\s*["\']([^"\']+)["\']')

# Extras that are build/dev tooling, not part of the distributed runtime: excluded from the SBOM
# so it describes what a *user* of the package can pull in, not the maintainer's toolchain.
_TOOLING_EXTRAS = {"dev", "release"}


def _wheel_metadata(wheel: Path) -> email.message.Message:
    with zipfile.ZipFile(wheel) as zf:
        name = next((n for n in zf.namelist() if n.endswith(".dist-info/METADATA")), None)
        if name is None:
            raise SystemExit(f"no .dist-info/METADATA in {wheel}")
        text = zf.read(name).decode("utf-8")
    return email.parser.Parser().parsestr(text)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dependency_components(meta: email.message.Message, package: str) -> list[dict]:
    components: list[dict] = []
    seen: set[str] = set()
    for raw in meta.get_all("Requires-Dist", []):
        spec, _, marker = raw.partition(";")
        m = _NAME_RE.match(spec.strip())
        if not m:
            continue
        name, version_spec = m.group(1), m.group(2).strip()
        extra = _EXTRA_RE.search(marker)
        # Skip the package's self-referential extras (`focus-data-toolkit[...] ; extra == "dev"`)
        # and build/dev tooling extras: the SBOM describes the distributed runtime, not the toolchain.
        if name.lower() == package.lower():
            continue
        if extra and extra.group(1) in _TOOLING_EXTRAS:
            continue
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        # All core runtime deps are optional extras (the core is standard-library only).
        comp: dict = {
            "type": "library",
            "bom-ref": f"pkg:pypi/{name.lower()}",
            "name": name,
            "purl": f"pkg:pypi/{name.lower()}",
            "scope": "optional",
        }
        if version_spec:
            comp["version"] = version_spec
        props = [{"name": "cdx:pip:requires-dist", "value": raw.strip()}]
        if extra:
            props.append({"name": "focus:extra", "value": extra.group(1)})
        comp["properties"] = props
        components.append(comp)
    return components


def _model_component() -> dict:
    prov = json.loads(_PROVENANCE.read_text(encoding="utf-8"))
    src = prov["source"]
    out = prov["output"]
    return {
        "type": "data",
        "bom-ref": "focus-1.4-data-model",
        "name": "FOCUS 1.4 data model",
        "version": prov.get("focus_version", "1.4"),
        "description": (
            "Embedded machine-readable FOCUS 1.4 data model (focus_1_4_model.json), a derivative of "
            "the FinOps FOCUS specification / data-model workbook."
        ),
        "licenses": [{"license": {"id": src.get("license", "CC-BY-4.0")}}],
        "hashes": [{"alg": "SHA-256", "content": out["sha256"]}],
        "externalReferences": [
            {"type": "website", "url": src.get("homepage", "https://focus.finops.org")},
            {"type": "vcs", "url": src.get("repository", "")},
        ],
        "properties": [
            {"name": "focus:provenance_status", "value": prov.get("provenance_status", "partial")},
            {"name": "focus:publisher", "value": src.get("publisher", "FinOps Foundation")},
        ],
    }


def build_sbom(wheel: Path, source_date_epoch: int | None) -> dict:
    meta = _wheel_metadata(wheel)
    name = meta.get("Name", "focus-data-toolkit")
    version = meta.get("Version", "0")
    license_expr = meta.get("License-Expression") or "MIT"
    purl = f"pkg:pypi/{name.lower()}@{version}"

    root = {
        "type": "application",
        "bom-ref": purl,
        "name": name,
        "version": version,
        "purl": purl,
        # A compound SPDX expression (e.g. "MIT AND CC-BY-4.0") uses the expression form; a
        # single id keeps the license/id form.
        "licenses": (
            [{"expression": license_expr}] if " " in license_expr
            else [{"license": {"id": license_expr}}]
        ),
    }
    model = _model_component()
    deps = _dependency_components(meta, name)
    components = sorted([model, *deps], key=lambda c: c["bom-ref"])

    metadata: dict = {
        "tools": [{"vendor": name, "name": "generate_sbom.py", "version": version}],
        "component": root,
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
        "dependencies": [
            {"ref": purl, "dependsOn": [c["bom-ref"] for c in components]},
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate a deterministic CycloneDX SBOM for a wheel.")
    ap.add_argument("wheel", type=Path, help="path to the built .whl")
    ap.add_argument("-o", "--output", type=Path, help="output path (default: stdout)")
    args = ap.parse_args(argv)

    sde = os.environ.get("SOURCE_DATE_EPOCH")
    sbom = build_sbom(args.wheel, int(sde) if sde else None)
    text = json.dumps(sbom, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
