"""Convert FOCUS 1.2/1.3 source data into the four FOCUS 1.4 datasets.

Two modes (see :mod:`focus_data_toolkit.modes`):

* ``STRICT`` (default) — a canonical FOCUS 1.4 dataset is produced only when every
  Mandatory non-nullable column has a factual lineage (observed / renamed / derived /
  enriched). Datasets that would require assumed provider-issued values are reported
  ``NOT_PRODUCED`` in the manifest, never fabricated. In practice only Cost and Usage is
  produced from a Cost-and-Usage source; Billing Period, Invoice Detail and the expanded
  1.4 Contract Commitment require provider billing facts absent from the source.
* ``SYNTHETIC`` — for demos / tests / learning: assumed values are generated, the affected
  datasets are labelled synthetic in the manifest (and filenames), and the result is never
  presented as fully conformant.

Every conversion emits a deterministic manifest recording, per column, how the value was
obtained.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from focus_data_toolkit import manifest as manifest_mod
from focus_data_toolkit.context import describe_source_contexts, representative_provider
from focus_data_toolkit.convert.billing_period import PROVENANCE as BILLING_PERIOD_PROVENANCE
from focus_data_toolkit.convert.billing_period import build_billing_periods
from focus_data_toolkit.convert.contract_commitment import (
    PROVENANCE as CONTRACT_COMMITMENT_PROVENANCE,
)
from focus_data_toolkit.convert.contract_commitment import convert_contract_commitment
from focus_data_toolkit.convert.cost_and_usage import (
    convert_cost_and_usage,
    cost_and_usage_provenance,
)
from focus_data_toolkit.convert.detect import detect_focus_version
from focus_data_toolkit.convert.invoice_detail import PROVENANCE as INVOICE_DETAIL_PROVENANCE
from focus_data_toolkit.convert.invoice_detail import build_invoice_details
from focus_data_toolkit.errors import Diagnostic, Severity
from focus_data_toolkit.io.atomic_writer import (
    AtomicOutputDir,
    AtomicWriteError,
    DestinationExistsError,
    OnExists,
    sha256sums_text,
)
from focus_data_toolkit.model import FOCUS_1_4_DATASETS, load_model
from focus_data_toolkit.model.validator import LintReport, lint_focus_1_4_structure
from focus_data_toolkit.modes import Mode
from focus_data_toolkit.provenance import ColumnRule, has_assumptions, strict_blockers
from focus_data_toolkit.schema import registry
from focus_data_toolkit.schema.detection import SchemaDetectionResult, detect_focus_schema

# Base output file name per dataset (stable, snake_case). Synthetic datasets are written
# with a ``synthetic_`` prefix so they are unmistakable on disk.
DATASET_FILENAMES = {
    "Cost and Usage": "focus_1_4_cost_and_usage.csv",
    "Contract Commitment": "focus_1_4_contract_commitment.csv",
    "Billing Period": "focus_1_4_billing_period.csv",
    "Invoice Detail": "focus_1_4_invoice_detail.csv",
}


class ConversionError(ValueError):
    """Raised when the source cannot be converted."""


@dataclass
class ConversionResult:
    """Outcome of a 1.x -> 1.4 conversion."""

    source_version: str
    mode: Mode
    datasets: dict[str, list[dict[str, str]]]
    provenance: dict[str, dict[str, ColumnRule]]
    manifest: dict
    reports: dict[str, LintReport] = field(default_factory=dict)
    detection: SchemaDetectionResult | None = None
    contexts: dict = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """All produced datasets passed the structural + semantic lint."""
        return all(r.ok for r in self.reports.values())

    @property
    def coverage(self) -> tuple[str, ...]:
        """FOCUS 1.4 datasets actually produced (in canonical order)."""
        return tuple(name for name in FOCUS_1_4_DATASETS if name in self.datasets)

    @property
    def not_produced(self) -> tuple[str, ...]:
        return tuple(name for name in FOCUS_1_4_DATASETS if name not in self.datasets)

    @property
    def assumptions_present(self) -> bool:
        return bool(self.manifest["assumptions_present"])

    def output_filename(self, dataset: str) -> str:
        return self.manifest["datasets"][dataset]["output_file"]


def _resolve_source_version(
    cau_rows: list[dict[str, str]],
    *,
    source_version: str | None,
    source_dataset: str | None,
    mode: Mode,
) -> tuple[str, SchemaDetectionResult]:
    """Determine the convertible source version and record the detection decision.

    ``source_version`` / ``source_dataset`` force the corresponding dimension. In strict mode
    an ambiguous or low-confidence detection (that is not forced) is refused with a clear
    error; a forced version incompatible with the header is always refused.
    """
    headers = cau_rows[0].keys()
    # A bad --source-version/--source-dataset value raises ValueError from normalisation;
    # surface it as a ConversionError so the CLI exits with the invalid-argument code, not a
    # traceback.
    try:
        detection = detect_focus_schema(headers, dataset=source_dataset, version=source_version)
    except ValueError as exc:
        raise ConversionError(f"invalid --source-version/--source-dataset: {exc}") from exc

    forced = source_version is not None or source_dataset is not None
    if forced and detection.confidence == "LOW":
        raise ConversionError(
            "forced source schema is incompatible with the header (detected "
            f"{detection.dataset} {detection.detected_version}, confidence LOW): "
            + "; ".join(detection.notes)
        )

    if source_version is not None:
        try:
            version = registry.normalize_version(source_version)
        except ValueError as exc:
            raise ConversionError(f"invalid --source-version {source_version!r}: {exc}") from exc
    else:
        if mode is Mode.STRICT and not forced and detection.confidence != "HIGH":
            raise ConversionError(
                "strict mode refuses an ambiguous or low-confidence source schema (detected "
                f"{detection.dataset} {detection.detected_version}, confidence "
                f"{detection.confidence}); force it with --source-version / --source-dataset"
            )
        # detect_focus_version raises a clear ValueError for non-CAU / 1.4 / non-FOCUS headers.
        try:
            version = detect_focus_version(headers)
        except ValueError as exc:
            raise ConversionError(str(exc)) from exc

    if version not in ("1.2", "1.3"):
        raise ConversionError(
            f"unsupported source version {version!r}; this tool converts FOCUS 1.2/1.3 -> 1.4"
        )
    return version, detection


def convert_to_focus_1_4(
    cau_rows: list[dict[str, str]],
    cc_rows: list[dict[str, str]] | None = None,
    *,
    source_version: str | None = None,
    source_dataset: str | None = None,
    mode: Mode | str = Mode.STRICT,
    validate: bool = True,
) -> ConversionResult:
    """Convert FOCUS 1.2/1.3 rows into the FOCUS 1.4 datasets for the given ``mode``.

    ``cau_rows`` is a FOCUS 1.2 or 1.3 Cost and Usage table; ``cc_rows`` is the optional
    FOCUS 1.3 Contract Commitment table. ``source_version`` / ``source_dataset`` force schema
    detection. Returns a :class:`ConversionResult` carrying the produced datasets, per-column
    provenance, the detected schema, a per-row context summary, diagnostics, a manifest and
    (when ``validate``) lint reports.
    """
    if not cau_rows:
        raise ConversionError("no Cost and Usage rows to convert")
    mode = Mode(mode)
    version, detection = _resolve_source_version(
        cau_rows, source_version=source_version, source_dataset=source_dataset, mode=mode
    )
    synthetic = mode is Mode.SYNTHETIC
    model = load_model()
    source_cols = set(cau_rows[0].keys())

    # Provider/issuer context is derived from the whole source, never the first row. A single
    # representative is needed only to enrich synthetic Contract Commitment (whose 1.3 source
    # carries no provider); ambiguity is surfaced as a diagnostic, never resolved silently.
    contexts = describe_source_contexts(cau_rows, version)
    provider_ctx, provider_ambiguous = representative_provider(cau_rows, version)
    issuers = sorted(
        {(r.get("InvoiceIssuerName") or "").strip() for r in cau_rows}
        - {""}
    )
    issuer = issuers[0] if issuers else provider_ctx.service_provider_name
    diagnostics: list[Diagnostic] = []

    # Synthetic-only builders (Billing Period / Invoice Detail / Contract Commitment are
    # never strictly producible from a Cost-and-Usage source).
    if synthetic:
        invoice_rows, id_mapping = build_invoice_details(cau_rows)
        billing_rows = build_billing_periods(cau_rows)
        if cc_rows:
            commitment_rows = convert_contract_commitment(
                cc_rows,
                service_provider_name=provider_ctx.service_provider_name,
                invoice_issuer_name=issuer,
            )
            if provider_ambiguous:
                diagnostics.append(
                    Diagnostic(
                        code="FDT-CTX-001",
                        severity=Severity.WARNING,
                        message="source carries multiple provider contexts; a representative "
                        "was chosen to enrich synthetic Contract Commitment",
                        datasets=("Contract Commitment",),
                        context={"chosen_service_provider": provider_ctx.service_provider_name},
                    )
                )
            if len(issuers) > 1:
                diagnostics.append(
                    Diagnostic(
                        code="FDT-CTX-002",
                        severity=Severity.WARNING,
                        message="source carries multiple invoice issuers; a representative was "
                        "chosen to enrich synthetic Contract Commitment",
                        datasets=("Contract Commitment",),
                        context={"chosen_invoice_issuer": issuer},
                    )
                )
        else:
            commitment_rows = None
    else:
        invoice_rows, id_mapping, billing_rows, commitment_rows = None, {}, None, None

    cu_rows = convert_cost_and_usage(cau_rows, version, invoice_detail_ids=id_mapping)
    cu_prov = cost_and_usage_provenance(source_cols, version, invoice_detail_linked=synthetic)

    provenance: dict[str, dict[str, ColumnRule]] = {
        "Cost and Usage": cu_prov,
        "Contract Commitment": CONTRACT_COMMITMENT_PROVENANCE,
        "Billing Period": BILLING_PERIOD_PROVENANCE,
        "Invoice Detail": INVOICE_DETAIL_PROVENANCE,
    }
    built_rows: dict[str, list[dict[str, str]] | None] = {
        "Cost and Usage": cu_rows,
        "Contract Commitment": commitment_rows,
        "Billing Period": billing_rows,
        "Invoice Detail": invoice_rows,
    }
    source_available = {
        "Cost and Usage": True,
        "Contract Commitment": bool(cc_rows),  # None or empty -> no source dataset
        "Billing Period": True,
        "Invoice Detail": True,
    }

    produced: dict[str, list[dict[str, str]]] = {}
    entries: dict[str, dict] = {}
    for name in FOCUS_1_4_DATASETS:
        prov = provenance[name]
        cols = model["datasets"][name]["columns"]
        base_filename = DATASET_FILENAMES[name]

        if not source_available[name]:
            entries[name] = manifest_mod.dataset_entry(
                status=manifest_mod.NOT_PRODUCED,
                conformance=manifest_mod.CONF_INCOMPLETE,
                provenance=prov,
                reason="no source dataset available for this FOCUS 1.4 dataset",
            )
            continue

        blockers = strict_blockers(prov, cols)
        if blockers and not synthetic:
            entries[name] = manifest_mod.dataset_entry(
                status=manifest_mod.NOT_PRODUCED,
                conformance=manifest_mod.CONF_INCOMPLETE,
                provenance=prov,
                reason="Mandatory provider-issued fields unavailable from Cost and Usage",
                blocking_columns=blockers,
            )
            continue

        rows = built_rows[name] or []
        if not rows:
            # The source carried no rows this dataset can be derived from (e.g. no
            # InvoiceId anywhere). Do not advertise a produced-but-empty (headerless) file.
            entries[name] = manifest_mod.dataset_entry(
                status=manifest_mod.NOT_PRODUCED,
                conformance=manifest_mod.CONF_INCOMPLETE,
                provenance=prov,
                reason="source rows yield no derivable rows for this dataset",
            )
            continue

        assumed = has_assumptions(prov) if synthetic else bool(blockers)
        status = manifest_mod.PRODUCED_SYNTHETIC if assumed else manifest_mod.PRODUCED
        # Conformance for a factual dataset is only known after the lint runs (below);
        # start it as NOT_VALIDATED. Synthetic datasets are never a lint claim.
        conformance = manifest_mod.CONF_SYNTHETIC if assumed else manifest_mod.CONF_NOT_VALIDATED
        output_file = f"synthetic_{base_filename}" if assumed else base_filename
        produced[name] = rows
        entries[name] = manifest_mod.dataset_entry(
            status=status,
            conformance=conformance,
            provenance=prov,
            row_count=len(rows),
            output_file=output_file,
        )

    from focus_data_toolkit import __version__

    manifest = manifest_mod.build_manifest(
        tool_version=__version__,
        source_version=version,
        mode=mode.value,
        datasets=entries,
        detection=detection.as_dict(),
        contexts=contexts,
        diagnostics=[d.as_dict() for d in diagnostics],
    )

    result = ConversionResult(
        source_version=version,
        mode=mode,
        datasets=produced,
        provenance=provenance,
        manifest=manifest,
        detection=detection,
        contexts=contexts,
        diagnostics=diagnostics,
    )
    if validate:
        for name, rows in produced.items():
            report = lint_focus_1_4_structure(name, rows)
            result.reports[name] = report
            entry = result.manifest["datasets"][name]
            # Only a factual dataset advertises a lint conclusion; set it now that the
            # lint has actually run (synthetic entries keep their SYNTHETIC label).
            if entry["conformance"] == manifest_mod.CONF_NOT_VALIDATED:
                entry["conformance"] = (
                    manifest_mod.CONF_STRUCTURAL_LINT if report.ok
                    else manifest_mod.CONF_LINT_FAILED
                )
    return result


def read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    """Read a CSV file into a list of dict rows (all values as strings)."""
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def rows_to_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    """Serialize dict rows to CSV bytes (column order taken from the first row)."""
    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


RUN_SIDECAR_FILENAME = "_run.json"
SHA256SUMS_FILENAME = "SHA256SUMS"


def _run_metadata(
    result: ConversionResult,
    checksums: dict[str, str],
    sizes: dict[str, int],
    run_id: str,
    tool_version: str,
    generated_at: str,
) -> dict:
    """Operational metadata sidecar — kept OUT of the deterministic business manifest.

    Carries the run id, wall-clock timestamp and per-file checksums/sizes/row-counts, so the
    business datasets and manifest stay byte-reproducible while operational facts are recorded.
    """
    file_to_dataset = {result.output_filename(name): name for name in result.datasets}
    files = []
    for filename in sorted(checksums):
        dataset = file_to_dataset.get(filename)
        entry = result.manifest["datasets"].get(dataset, {}) if dataset else {}
        files.append(
            {
                "name": filename,
                "dataset": dataset,
                "format": "csv",
                "row_count": len(result.datasets.get(dataset, [])) if dataset else None,
                "size_bytes": sizes.get(filename),
                "sha256": checksums[filename],
                "status": entry.get("status"),
                "conformance": entry.get("conformance"),
            }
        )
    return {
        "run_id": run_id,
        "generated_at": generated_at,
        "toolkit_version": tool_version,
        "mode": result.mode.value,
        "source_version": result.source_version,
        "manifest": manifest_mod.MANIFEST_FILENAME,
        "files": files,
    }


def write_result(
    result: ConversionResult,
    out_dir: str | Path,
    *,
    on_exists: OnExists | str = OnExists.REFUSE,
    keep_temp: bool = False,
    require_valid: bool = True,
) -> list[Path]:
    """Write every produced dataset plus the manifest to ``out_dir`` **atomically**.

    Files are staged in a temporary directory on the same filesystem; only after mandatory
    validation passes and the manifest, checksums and operational sidecar are written is the
    directory published with a single atomic rename. On any error the staging directory is
    removed and ``out_dir`` is left untouched (existing results are never partially clobbered).

    ``on_exists`` chooses the policy when ``out_dir`` already exists (refuse / replace /
    version). ``require_valid`` refuses to publish when the built-in lint failed. Returns the
    published dataset + manifest paths.
    """
    from focus_data_toolkit import __version__

    generated_at = datetime.now(UTC).isoformat()
    data_files = [
        (result.output_filename(name), rows_to_csv_bytes(rows))
        for name, rows in result.datasets.items()
    ]

    with AtomicOutputDir(out_dir, on_exists=on_exists, keep_temp=keep_temp) as out:
        for name, data in data_files:
            out.write_bytes(name, data)

        # Mandatory validation gate: never publish a lint-failing result.
        if require_valid and result.reports and not result.ok:
            failed = sorted(n for n, r in result.reports.items() if not r.ok)
            raise AtomicWriteError(
                f"lint failed for {failed}; final output not written to {out_dir}"
            )

        checksums = out.checksums()
        manifest_bytes = manifest_mod.render(result.manifest).encode("utf-8")
        sidecar = _run_metadata(
            result, checksums, out.sizes(), out.run_id, __version__, generated_at
        )
        all_sums = dict(checksums)
        all_sums[manifest_mod.MANIFEST_FILENAME] = hashlib.sha256(manifest_bytes).hexdigest()
        final_files = {
            manifest_mod.MANIFEST_FILENAME: manifest_bytes,
            RUN_SIDECAR_FILENAME: (json.dumps(sidecar, indent=2, sort_keys=True) + "\n").encode(),
            SHA256SUMS_FILENAME: sha256sums_text(all_sums).encode("utf-8"),
        }
        target = out.commit(final_files=final_files)

    written = [target / name for name, _ in data_files]
    written.append(target / manifest_mod.MANIFEST_FILENAME)
    return written


__all__ = [
    "DATASET_FILENAMES",
    "AtomicWriteError",
    "ConversionError",
    "ConversionResult",
    "DestinationExistsError",
    "OnExists",
    "convert_to_focus_1_4",
    "detect_focus_version",
    "read_csv_rows",
    "rows_to_csv_bytes",
    "write_result",
]
