"""Deterministic, machine-readable conversion manifest.

The manifest records, per FOCUS 1.4 dataset, whether it was produced and — column by
column — how each value was obtained (see :mod:`focus_data_toolkit.provenance`). It is
built without a clock or RNG so a given conversion always yields the same manifest bytes.
Any produced column with ``ASSUMED`` lineage sets ``assumptions_present`` and prevents a
full-conformance claim.
"""

from __future__ import annotations

import json

from focus_data_toolkit.provenance import ColumnRule

MANIFEST_FILENAME = "focus_1_4_manifest.json"
TARGET_VERSION = "1.4"

# Dataset statuses.
PRODUCED = "PRODUCED"
PRODUCED_SYNTHETIC = "PRODUCED_SYNTHETIC"
NOT_PRODUCED = "NOT_PRODUCED"

# Conformance labels (never "fully conformant": see model/validator.py levels).
CONF_STRUCTURAL_LINT = "STRUCTURAL_LINT"  # produced from factual lineage, passed the lint
CONF_LINT_FAILED = "LINT_FAILED"          # produced from factual lineage, but the lint failed
CONF_NOT_VALIDATED = "NOT_VALIDATED"      # produced but lint not run (validate=False)
CONF_SYNTHETIC = "SYNTHETIC"              # produced with assumed values
CONF_INCOMPLETE = "INCOMPLETE"            # not produced


def dataset_entry(
    *,
    status: str,
    conformance: str,
    provenance: dict[str, ColumnRule],
    row_count: int | None = None,
    reason: str | None = None,
    blocking_columns: list[str] | None = None,
    output_file: str | None = None,
) -> dict:
    entry: dict = {
        "status": status,
        "conformance": conformance,
        "columns": {col: provenance[col].as_dict() for col in sorted(provenance)},
    }
    if row_count is not None:
        entry["row_count"] = row_count
    if reason:
        entry["reason"] = reason
    if blocking_columns:
        entry["blocking_columns"] = blocking_columns
    if output_file:
        entry["output_file"] = output_file
    return entry


def build_manifest(
    *,
    tool_version: str,
    source_version: str,
    mode: str,
    datasets: dict[str, dict],
    detection: dict | None = None,
    contexts: dict | None = None,
    diagnostics: list[dict] | None = None,
) -> dict:
    assumptions_present = any(
        entry["status"] in (PRODUCED, PRODUCED_SYNTHETIC)
        and any(col.get("lineage") == "ASSUMED" for col in entry.get("columns", {}).values())
        for entry in datasets.values()
    )
    manifest: dict = {
        "tool_version": tool_version,
        "source_version": source_version,
        "target_version": TARGET_VERSION,
        "mode": mode,
        "assumptions_present": assumptions_present,
        "datasets": datasets,
    }
    # Detection decision and per-row context summary are part of the deterministic business
    # record (no clock/RNG). Diagnostics record non-fatal findings (e.g. ambiguous context).
    if detection is not None:
        manifest["detection"] = detection
    if contexts is not None:
        manifest["contexts"] = contexts
    if diagnostics is not None:
        manifest["diagnostics"] = diagnostics
    return manifest


def render(manifest: dict) -> str:
    """Serialize the manifest to deterministic JSON bytes-as-text (sorted, trailing LF)."""
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"
