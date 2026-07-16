"""Streaming, bounded-memory conversion of large Cost and Usage files.

``convert_files`` reads the Cost and Usage CSV once, writing the converted Cost and Usage
output incrementally and staging the Invoice Detail aggregation / Billing Period dedup in a
throwaway SQLite database (:mod:`focus_data_toolkit.storage.external_index`). Memory stays
bounded — one row plus one running group accumulator plus SQLite's capped page cache — so
files far larger than RAM convert successfully.

Equivalence with the eager :func:`focus_data_toolkit.convert.convert_to_focus_1_4` path is by
construction: both call the same pure per-row / per-group functions
(``convert_cost_and_usage_row``, ``invoice_detail_row``, ``billing_period_row``) and the same
manifest assembler (``assemble_manifest``). The output is byte-identical.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import astuple, replace
from datetime import UTC, datetime
from pathlib import Path

from focus_data_toolkit import manifest as manifest_mod
from focus_data_toolkit.context import (
    billing_context_of_row,
    provider_context_of_row,
    representative_from_contexts,
    summarize_contexts,
)
from focus_data_toolkit.context.billing import BillingContext
from focus_data_toolkit.context.provider import ProviderContext
from focus_data_toolkit.convert import (
    DATASET_FILENAMES,
    RUN_SIDECAR_FILENAME,
    SHA256SUMS_FILENAME,
    ConversionError,
    assemble_manifest,
    read_csv_rows,
)
from focus_data_toolkit.convert.billing_period import PROVENANCE as BILLING_PERIOD_PROVENANCE
from focus_data_toolkit.convert.billing_period import billing_period_row
from focus_data_toolkit.convert.contract_commitment import (
    PROVENANCE as CONTRACT_COMMITMENT_PROVENANCE,
)
from focus_data_toolkit.convert.contract_commitment import convert_contract_commitment
from focus_data_toolkit.convert.cost_and_usage import (
    convert_cost_and_usage_row,
    cost_and_usage_provenance,
)
from focus_data_toolkit.convert.invoice_detail import PROVENANCE as INVOICE_DETAIL_PROVENANCE
from focus_data_toolkit.convert.invoice_detail import (
    emitted_invoice_detail_columns,
    invoice_detail_grain_key,
    invoice_detail_id,
    invoice_detail_row,
)
from focus_data_toolkit.errors import Diagnostic, Severity
from focus_data_toolkit.io.atomic_writer import AtomicOutputDir, AtomicWriteError, OnExists
from focus_data_toolkit.io.csv_io import CsvRowReader, open_csv_writer
from focus_data_toolkit.io.records import DatasetSchema
from focus_data_toolkit.model import dataset_columns, load_model
from focus_data_toolkit.modes import Mode
from focus_data_toolkit.provenance import has_assumptions, strict_blockers

# Rows per chunk when linting a produced file (bounded memory; the linter has no cross-row
# state, so chunked linting equals whole-file linting for the fixed model column set).
_LINT_CHUNK = 5000

_INDEX_DB = "_index.sqlite"


def _dataset_is_assumed(name: str, provenance: dict, synthetic: bool) -> bool:
    prov = provenance[name]
    cols = load_model()["datasets"][name]["columns"]
    return has_assumptions(prov) if synthetic else bool(strict_blockers(prov, cols))


def _output_filename(name: str, provenance: dict, synthetic: bool) -> str:
    prefix = "synthetic_" if _dataset_is_assumed(name, provenance, synthetic) else ""
    return prefix + DATASET_FILENAMES[name]


def _lint_file(dataset: str, path: Path):
    """Lint a produced CSV in bounded chunks, returning a merged LintReport."""
    from focus_data_toolkit.model.validator import (
        _CHECKED_LEVELS,
        LintReport,
        lint_focus_1_4_structure,
    )

    reader = CsvRowReader(path)
    violations: list = []
    total = 0
    levels = _CHECKED_LEVELS
    chunk: list[dict[str, str]] = []

    def flush(rows: list[dict[str, str]]) -> None:
        nonlocal total, levels
        report = lint_focus_1_4_structure(dataset, rows)
        levels = report.levels_checked
        for v in report.violations:
            if v.row_index is None:
                violations.append(v)
            else:
                violations.append(replace(v, row_index=v.row_index + total))
        total += len(rows)

    try:
        for record in reader:
            chunk.append(record.values)
            if len(chunk) >= _LINT_CHUNK:
                flush(chunk)
                chunk = []
        if chunk:
            flush(chunk)
    finally:
        reader.close()
    return LintReport(dataset=dataset, row_count=total, violations=violations, levels_checked=levels)


def convert_files(
    cost_and_usage: str | os.PathLike[str],
    out_dir: str | os.PathLike[str],
    *,
    contract_commitment: str | os.PathLike[str] | None = None,
    source_version: str | None = None,
    source_dataset: str | None = None,
    mode: Mode | str = Mode.STRICT,
    validate: bool = True,
    on_exists: OnExists | str = OnExists.REFUSE,
    keep_temp: bool = False,
) -> Path:
    """Stream-convert a Cost and Usage file to the FOCUS 1.4 datasets in ``out_dir``.

    The Cost and Usage file is read once; Invoice Detail / Billing Period aggregation happens
    on disk (SQLite) so memory stays bounded. Output is published atomically (nothing appears
    until validation passes and checksums + manifest are written). Returns the published path.
    """
    from focus_data_toolkit import __version__
    from focus_data_toolkit.convert import _resolve_source_version

    mode = Mode(mode)
    synthetic = mode is Mode.SYNTHETIC
    generated_at = datetime.now(UTC).isoformat()

    reader = CsvRowReader(cost_and_usage)
    try:
        version, detection = _resolve_source_version(
            reader.source_columns,
            source_version=source_version,
            source_dataset=source_dataset,
            mode=mode,
        )
    except ConversionError:
        reader.close()
        raise

    cc_rows = read_csv_rows(contract_commitment) if contract_commitment else None
    source_cols = set(reader.source_columns)
    provenance = {
        "Cost and Usage": cost_and_usage_provenance(
            source_cols, version, invoice_detail_linked=synthetic
        ),
        "Contract Commitment": CONTRACT_COMMITMENT_PROVENANCE,
        "Billing Period": BILLING_PERIOD_PROVENANCE,
        "Invoice Detail": INVOICE_DETAIL_PROVENANCE,
    }
    diagnostics: list[Diagnostic] = []
    row_counts = dict.fromkeys(load_model()["datasets"], 0)

    with AtomicOutputDir(out_dir, on_exists=on_exists, keep_temp=keep_temp) as out:
        cu_columns = dataset_columns("Cost and Usage")
        cu_file = _output_filename("Cost and Usage", provenance, synthetic)
        cu_handle, cu_writer = open_csv_writer(
            out.path_for(cu_file), DatasetSchema("Cost and Usage", cu_columns)
        )
        index = ExternalIndexOpener(out.path_for(_INDEX_DB)) if synthetic else None

        provider_seen: dict[tuple[str, str], ProviderContext] = {}
        billing_seen: dict[tuple, BillingContext] = {}
        cu_count = 0

        try:
            for record in reader:
                row = record.values
                pctx = provider_context_of_row(row, version)
                provider_seen[(pctx.service_provider_name, pctx.host_provider_name)] = pctx
                bctx = billing_context_of_row(row)
                billing_seen[astuple(bctx)] = bctx

                grain = invoice_detail_grain_key(row)
                detail_id = invoice_detail_id(grain) if (synthetic and grain[1]) else ""
                cu_writer.write(
                    convert_cost_and_usage_row(row, version, detail_id=detail_id, target=cu_columns)
                )
                cu_count += 1

                if synthetic:
                    if grain[1]:
                        index.stage_invoice_line(grain, (row.get("BilledCost") or "0"))
                    start = (row.get("BillingPeriodStart") or "").strip()
                    end = (row.get("BillingPeriodEnd") or "").strip()
                    issuer = (row.get("InvoiceIssuerName") or "").strip()
                    if start and end:
                        index.stage_billing_period(start, end, issuer)
        finally:
            cu_handle.close()
            reader.close()
        row_counts["Cost and Usage"] = cu_count

        staged = {"Cost and Usage": cu_file}

        if synthetic:
            emitted = emitted_invoice_detail_columns()
            id_file = _output_filename("Invoice Detail", provenance, synthetic)
            id_handle, id_writer = open_csv_writer(
                out.path_for(id_file), DatasetSchema("Invoice Detail", tuple(emitted))
            )
            id_count = 0
            for grain, total in index.finalize_invoice_groups():
                id_writer.write(invoice_detail_row(grain, total, invoice_detail_id(grain), emitted))
                id_count += 1
            id_handle.close()
            staged["Invoice Detail"] = id_file
            row_counts["Invoice Detail"] = id_count

            bp_columns = dataset_columns("Billing Period")
            bp_file = _output_filename("Billing Period", provenance, synthetic)
            bp_handle, bp_writer = open_csv_writer(
                out.path_for(bp_file), DatasetSchema("Billing Period", bp_columns)
            )
            bp_count = 0
            for start, end, issuer in index.finalize_billing_periods():
                bp_writer.write(billing_period_row(start, end, issuer, bp_columns))
                bp_count += 1
            bp_handle.close()
            staged["Billing Period"] = bp_file
            row_counts["Billing Period"] = bp_count

            if cc_rows:
                providers = [provider_seen[k] for k in sorted(provider_seen)]
                provider_ctx, provider_ambiguous = representative_from_contexts(providers)
                issuers = sorted(
                    {b.invoice_issuer_name for b in billing_seen.values() if b.invoice_issuer_name}
                )
                issuer = issuers[0] if issuers else provider_ctx.service_provider_name
                cc_out = convert_contract_commitment(
                    cc_rows,
                    service_provider_name=provider_ctx.service_provider_name,
                    invoice_issuer_name=issuer,
                )
                cc_columns = dataset_columns("Contract Commitment")
                cc_file = _output_filename("Contract Commitment", provenance, synthetic)
                cc_handle, cc_writer = open_csv_writer(
                    out.path_for(cc_file), DatasetSchema("Contract Commitment", cc_columns)
                )
                for r in cc_out:
                    cc_writer.write(r)
                cc_handle.close()
                staged["Contract Commitment"] = cc_file
                row_counts["Contract Commitment"] = len(cc_out)
                diagnostics.extend(_context_diagnostics(provider_ambiguous, provider_ctx, issuers))

        if index is not None:
            index.close()

        providers = [provider_seen[k] for k in sorted(provider_seen)]
        billing = [billing_seen[k] for k in sorted(billing_seen)]
        contexts = summarize_contexts(providers, billing)

        source_available = {
            "Cost and Usage": True,
            "Contract Commitment": bool(cc_rows),
            "Billing Period": True,
            "Invoice Detail": True,
        }
        entries, manifest, produced_output_files = assemble_manifest(
            version=version,
            mode=mode,
            synthetic=synthetic,
            detection=detection,
            contexts=contexts,
            diagnostics=diagnostics,
            provenance=provenance,
            source_available=source_available,
            row_counts=row_counts,
        )

        # Remove any staged file whose dataset turned out NOT produced (e.g. zero derivable rows).
        for name, fname in staged.items():
            if name not in produced_output_files:
                out.path_for(fname).unlink(missing_ok=True)

        # Mandatory validation gate (chunked, bounded memory): never publish a lint failure.
        if validate:
            for name, fname in produced_output_files.items():
                report = _lint_file(name, out.path_for(fname))
                entry = manifest["datasets"][name]
                if entry["conformance"] == manifest_mod.CONF_NOT_VALIDATED:
                    entry["conformance"] = (
                        manifest_mod.CONF_STRUCTURAL_LINT if report.ok
                        else manifest_mod.CONF_LINT_FAILED
                    )
                if not report.ok:
                    raise AtomicWriteError(
                        f"lint failed for {name}; final output not written to {out_dir}"
                    )

        checksums = out.checksums()
        manifest_bytes = manifest_mod.render(manifest).encode("utf-8")
        sidecar = _run_metadata(
            produced_output_files, row_counts, manifest, out.run_id, __version__, generated_at, mode
        )
        all_sums = dict(checksums)
        all_sums[manifest_mod.MANIFEST_FILENAME] = hashlib.sha256(manifest_bytes).hexdigest()
        from focus_data_toolkit.io.atomic_writer import sha256sums_text

        final_files = {
            manifest_mod.MANIFEST_FILENAME: manifest_bytes,
            RUN_SIDECAR_FILENAME: (json.dumps(sidecar, indent=2, sort_keys=True) + "\n").encode(),
            SHA256SUMS_FILENAME: sha256sums_text(all_sums).encode("utf-8"),
        }
        return out.commit(final_files=final_files)


def _context_diagnostics(
    provider_ambiguous: bool, provider_ctx: ProviderContext, issuers: list[str]
) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    if provider_ambiguous:
        out.append(
            Diagnostic(
                code="FDT-CTX-001",
                severity=Severity.WARNING,
                message="source carries multiple provider contexts; a representative was chosen "
                "to enrich synthetic Contract Commitment",
                datasets=("Contract Commitment",),
                context={"chosen_service_provider": provider_ctx.service_provider_name},
            )
        )
    if len(issuers) > 1:
        out.append(
            Diagnostic(
                code="FDT-CTX-002",
                severity=Severity.WARNING,
                message="source carries multiple invoice issuers; a representative was chosen to "
                "enrich synthetic Contract Commitment",
                datasets=("Contract Commitment",),
                context={"chosen_invoice_issuer": issuers[0]},
            )
        )
    return out


def _run_metadata(
    produced_output_files: dict[str, str],
    row_counts: dict[str, int],
    manifest: dict,
    run_id: str,
    tool_version: str,
    generated_at: str,
    mode: Mode,
) -> dict:
    files = []
    for name, filename in sorted(produced_output_files.items(), key=lambda kv: kv[1]):
        entry = manifest["datasets"].get(name, {})
        files.append(
            {
                "name": filename,
                "dataset": name,
                "format": "csv",
                "row_count": row_counts.get(name),
                "status": entry.get("status"),
                "conformance": entry.get("conformance"),
            }
        )
    return {
        "run_id": run_id,
        "generated_at": generated_at,
        "toolkit_version": tool_version,
        "mode": mode.value,
        "source_version": manifest.get("source_version"),
        "manifest": manifest_mod.MANIFEST_FILENAME,
        "files": files,
    }


def ExternalIndexOpener(db_path: Path):  # noqa: N802 - factory reads clearly at call site
    """Open an :class:`ExternalIndex` (imported lazily to keep the import graph shallow)."""
    from focus_data_toolkit.storage.external_index import ExternalIndex

    return ExternalIndex(db_path)


__all__ = ["convert_files"]
