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
import shutil
import time
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import astuple, replace
from datetime import UTC, datetime
from pathlib import Path

from focus_data_toolkit import manifest as manifest_mod
from focus_data_toolkit import runtime
from focus_data_toolkit.context import (
    billing_context_of_row,
    provider_context_of_row,
    representative_from_contexts,
    summarize_contexts,
)
from focus_data_toolkit.context.billing import BillingContext
from focus_data_toolkit.context.provider import ProviderContext
from focus_data_toolkit.convert import (
    OUTPUT_FORMATS,
    RUN_SIDECAR_FILENAME,
    SHA256SUMS_FILENAME,
    ConversionCancelled,
    ConversionError,
    assemble_manifest,
    output_filename_for,
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
from focus_data_toolkit.io.row_source import open_row_source, read_source_rows
from focus_data_toolkit.model import dataset_columns, load_model
from focus_data_toolkit.model.capabilities import CapabilityProfile
from focus_data_toolkit.modes import Mode
from focus_data_toolkit.progress import CancelPredicate, ProgressCallback, ProgressEvent
from focus_data_toolkit.provenance import (
    ColumnRule,
    Lineage,
    LineageCounters,
    has_assumptions,
    strict_blockers,
)
from focus_data_toolkit.supplement.apply import (
    apply_billing_periods,
    apply_contract_commitments,
    apply_invoice_details,
    flip_enriched_rules,
)
from focus_data_toolkit.supplement.loader import SupplementBundle
from focus_data_toolkit.supplement.validate import (
    SourceKeySets,
    coverage,
    validate_supplements,
)

# Rows per chunk when linting a produced file (bounded memory; the linter has no cross-row
# state, so chunked linting equals whole-file linting for the fixed model column set).
_LINT_CHUNK = 5000

_INDEX_DB = "_index.sqlite"
_BUNDLE_DB = "_bundle_index.sqlite"

# Cancel is checked, and a ProgressEvent considered, on this row cadence; the emitted events
# are further time-throttled (below) so a fast conversion cannot flood the callback. Capped so
# a large ``progress_interval`` never makes cancellation unresponsive.
_PROGRESS_STEP_MAX = 5000
_PROGRESS_MIN_SECONDS = 0.5


def _unlink_db(path: Path) -> None:
    """Remove a scratch SQLite DB and any sidecar (-wal/-shm/-journal), ignoring absence."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            Path(str(path) + suffix).unlink()
        except OSError:
            pass  # best-effort cleanup: a missing/locked sidecar is not an error


def _progress_totals(reader, progress) -> tuple[str, int | None]:
    """(unit, total) for a source reader, computed **once** — bytes for CSV, rows for Parquet.

    Returns ``("rows", None)`` when no progress callback is set, so a partitioned-Parquet source
    is never row-counted (``count_rows()``) purely for metrics that no one consumes.
    """
    if progress is None:
        return "rows", None
    bytes_total = getattr(reader, "bytes_total", None)
    if bytes_total:
        return "bytes", bytes_total
    return "rows", getattr(reader, "expected_rows", None)


def _source_completed(reader, unit: str, count: int) -> int:
    """Completed amount for a source reader given the chosen unit (live byte cursor or row count)."""
    if unit == "bytes":
        return getattr(reader, "bytes_read", None) or count
    return count


def _discard_scratch(out, config, name: str, path: Path) -> None:
    """Drop a scratch DB: from the staging dir by name (default) or by path when relocated.

    A relocated scratch (under ``FOCUS_TOOLKIT_WORK_DIR``) is not inside the atomic staging dir,
    so ``AtomicOutputDir`` never publishes or cleans it — we unlink it explicitly here (and again,
    defensively, on any error via the ExitStack callback).
    """
    if config.work_dir is None:
        out.discard(name)  # scratch DB inside staging must never be published
    else:
        _unlink_db(path)


class _StagedRows:
    """Re-iterable row stream over a staged output file (or partition tree).

    Each ``__iter__`` opens a fresh reader, so the bundle validator can run several
    independent forward passes without ever materialising the dataset.
    """

    def __init__(
        self, path: Path, output_format: str, dataset: str, partition_by=None,
        *, check=None, guard=None,
    ) -> None:
        self._path = path
        self._output_format = output_format
        self._dataset = dataset
        self._partition_by = partition_by
        self._check = check
        self._guard = guard

    def __iter__(self):
        reader = _open_reader(
            self._path, self._output_format,
            dataset=self._dataset, partition_by=self._partition_by,
        )
        try:
            n = 0
            for record in reader:
                n += 1
                if n % _PROGRESS_STEP_MAX == 0:
                    if self._check is not None:
                        self._check()
                    if self._guard is not None:
                        self._guard()  # enforce disk budgets as the bundle spill DB grows
                yield record.values
        finally:
            reader.close()


def _dataset_is_assumed(name: str, provenance: dict, synthetic: bool) -> bool:
    prov = provenance[name]
    cols = load_model()["datasets"][name]["columns"]
    return has_assumptions(prov) if synthetic else bool(strict_blockers(prov, cols))


def _output_filename(
    name: str, provenance: dict, synthetic: bool, output_format: str, partitioned: bool = False
) -> str:
    assumed = _dataset_is_assumed(name, provenance, synthetic)
    return output_filename_for(
        name, synthetic_prefix=assumed, output_format=output_format, partitioned=partitioned
    )


def _open_writer(
    path: Path,
    schema: DatasetSchema,
    output_format: str,
    metadata=None,
    *,
    compression: str = "snappy",
    partition_by: tuple[str, ...] | None = None,
    target_file_size: int | None = None,
):
    """Open a format-appropriate row writer, returning ``(handle, writer)``.

    ``partition_by`` (Parquet only) writes a Hive-partitioned dataset directory instead of a
    single file; ``handle`` then equals the writer (it owns its own files).
    """
    if output_format == "parquet":
        from focus_data_toolkit.io.parquet_io import PartitionedParquetWriter, open_parquet_writer

        if partition_by:
            w = PartitionedParquetWriter(
                path,
                schema,
                partition_by,
                metadata=metadata,
                compression=compression,
                target_file_size=target_file_size,
            )
            return w, w
        return open_parquet_writer(path, schema, metadata=metadata, compression=compression)
    return open_csv_writer(path, schema)


def _open_reader(
    path: Path, output_format: str, *, dataset: str | None = None, partition_by=None
):
    """Open a format-appropriate row reader."""
    if output_format == "parquet":
        from focus_data_toolkit.io.parquet_io import ParquetRowReader, PartitionedParquetReader

        if partition_by:
            return PartitionedParquetReader(path, dataset or "Cost and Usage", partition_by)
        return ParquetRowReader(path, dataset=dataset)
    return CsvRowReader(path)


def _lint_file(
    dataset: str,
    path: Path,
    output_format: str = "csv",
    *,
    partition_by=None,
    capabilities: CapabilityProfile | None = None,
    check=None,
    on_rows=None,
):
    """Lint a produced file (or partition tree) in bounded chunks, returning a merged LintReport.

    ``check`` (a no-arg callable) is invoked per chunk to honour cancellation; ``on_rows`` (an
    ``int -> None`` callable) receives the running row count for progress reporting.
    """
    from focus_data_toolkit.model.validator import (
        _CHECKED_LEVELS,
        LintReport,
        lint_focus_1_4_structure,
    )

    reader = _open_reader(path, output_format, dataset=dataset, partition_by=partition_by)
    violations: list = []
    total = 0
    levels = _CHECKED_LEVELS
    chunk: list[dict[str, str]] = []

    def flush(rows: list[dict[str, str]]) -> None:
        nonlocal total, levels
        report = lint_focus_1_4_structure(dataset, rows, profile=capabilities)
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
                if check is not None:
                    check()
                if on_rows is not None:
                    on_rows(total)
        if chunk:
            flush(chunk)
    finally:
        reader.close()
    return LintReport(
        dataset=dataset, row_count=total, violations=tuple(violations), levels_checked=levels
    )


def _validate_parquet_options(
    output_format: str,
    partition_by: tuple[str, ...],
    compression: str,
    target_file_size: int | None,
) -> None:
    """Reject partitioning/compression options that don't apply or aren't valid FOCUS keys."""
    if output_format != "parquet":
        if partition_by:
            raise ConversionError("--partition-by requires --output-format parquet")
        return
    from focus_data_toolkit.io.parquet_io import COMPRESSIONS, partitionable_columns

    if compression not in COMPRESSIONS:
        raise ConversionError(
            f"unsupported compression {compression!r}; choose one of {', '.join(COMPRESSIONS)}"
        )
    if target_file_size is not None and target_file_size <= 0:
        raise ConversionError(
            f"--target-file-size must be positive, got {target_file_size} bytes"
        )
    if partition_by:
        bad = partitionable_columns("Cost and Usage", partition_by)
        if bad:
            raise ConversionError(
                "cannot partition on "
                + ", ".join(bad)
                + ": partition columns must be Cost and Usage String or Date/Time columns "
                "(not measures, JSON, or unknown columns)"
            )


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
    output_format: str = "csv",
    partition_by: Sequence[str] | None = None,
    compression: str = "snappy",
    target_file_size: int | None = None,
    capabilities: CapabilityProfile | None = None,
    supplements: SupplementBundle | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelPredicate | None = None,
    progress_interval: int = 5000,
) -> Path:
    """Stream-convert a Cost and Usage file to the FOCUS 1.4 datasets in ``out_dir``.

    Inputs (``cost_and_usage``, ``contract_commitment``) may each be CSV (gzip ok) or
    Parquet — the format is sniffed per file, so they can be mixed freely.
    The Cost and Usage file is read once (twice with ``supplements``: a cheap key-collection
    pre-pass validates the bundle before anything is staged); Invoice Detail / Billing Period
    aggregation happens on disk (SQLite) so memory stays bounded by the *supplement-scale*
    cardinalities (periods, invoices, invoice lines, commitments), never by the Cost and
    Usage row count. Output is published atomically (nothing appears until validation passes
    and checksums + manifest are written). ``output_format`` is ``csv`` (byte-exact) or
    ``parquet`` (value-exact decimal128; requires the ``[parquet]`` extra).

    Parquet only: ``partition_by`` writes the Cost and Usage dataset as a Hive-partitioned tree
    on the given low-cardinality String/Date-Time columns; ``compression`` selects the codec; and
    ``target_file_size`` (approximate uncompressed bytes) rolls each partition to a new part file.
    Returns the published path. Supplement handling is shared with the eager path
    (same ``apply_*`` functions), so both produce identical bytes.

    ``progress`` (an optional callback) receives throttled :class:`~focus_data_toolkit.progress.ProgressEvent`\\ s
    per phase; ``cancel`` (an optional predicate) is checked cooperatively between rows and
    validation passes — when it returns True the conversion raises
    :class:`~focus_data_toolkit.convert.ConversionCancelled` and the atomic staging directory is
    removed, so **nothing partial is ever published**. Both default to ``None`` (unchanged
    behaviour). ``progress_interval`` is the row cadence (capped at 5000) at which cancel is
    checked and progress considered.
    """
    from focus_data_toolkit import __version__
    from focus_data_toolkit.convert import _resolve_source_version

    if output_format not in OUTPUT_FORMATS:
        raise ConversionError(
            f"unsupported output format {output_format!r}; choose one of {', '.join(OUTPUT_FORMATS)}"
        )
    partition_by = tuple(partition_by or ())
    _validate_parquet_options(output_format, partition_by, compression, target_file_size)
    # Partitioning applies to the (large) Cost and Usage dataset only; the small derived datasets
    # stay single files.
    partition_map: dict[str, tuple[str, ...]] = (
        {"Cost and Usage": partition_by} if partition_by else {}
    )
    mode = Mode(mode)
    synthetic = mode is Mode.SYNTHETIC
    generated_at = datetime.now(UTC).isoformat()

    # --- runtime disk budgets (two filesystems: work scratch vs output staging) ----------
    config = runtime.RuntimeConfig.from_env()
    config.apply_logging()
    out_parent = Path(out_dir).parent
    work_dir_eff = config.work_dir or out_parent
    scratch_paths: list[Path] = []  # scratch DB files, tracked for MAX_WORK_BYTES accounting
    # Best-effort pre-flight before any staging: fail fast with a structured FDT-IO-005/006
    # diagnostic (exit 5) rather than a raw OSError mid-run.
    runtime.preflight(config, out_parent, [cost_and_usage, contract_commitment])

    def _resource_guard() -> None:
        scratch_bytes = 0
        for p in scratch_paths:
            try:
                scratch_bytes += p.stat().st_size
            except OSError:
                pass  # scratch DB may not exist yet / already discarded — not an error
        runtime.enforce_limits(config, out_parent, work_dir_eff, scratch_bytes)

    # --- progress + cooperative cancellation (opt-in; no-ops when unset) -----------------
    step = max(1, min(int(progress_interval or _PROGRESS_STEP_MAX), _PROGRESS_STEP_MAX))
    _last_emit_t = 0.0
    _last_emit_completed = 0
    _last_emit_phase: str | None = None

    def _check() -> None:
        if cancel is not None and cancel():
            raise ConversionCancelled("conversion cancelled")

    def _emit(
        phase: str,
        completed: int,
        total: int | None = None,
        unit: str = "rows",
        message: str | None = None,
        *,
        force: bool = False,
    ) -> None:
        nonlocal _last_emit_t, _last_emit_completed, _last_emit_phase
        if progress is None:
            return
        now = time.monotonic()
        # Fire on a phase boundary, once the completed count advances by the interval, or at
        # least every _PROGRESS_MIN_SECONDS — so fast runs still show movement and slow phases
        # still tick, without a callback per row.
        due = (
            force
            or phase != _last_emit_phase
            or completed - _last_emit_completed >= progress_interval
            or now - _last_emit_t >= _PROGRESS_MIN_SECONDS
        )
        if not due:
            return
        _last_emit_t = now
        _last_emit_completed = completed
        _last_emit_phase = phase
        try:
            progress(
                ProgressEvent(
                    phase=phase,  # type: ignore[arg-type]
                    completed=completed,
                    total=total,
                    unit=unit,
                    message=message,
                )
            )
        except Exception:  # a misbehaving progress sink must never break a conversion
            pass

    def _meta(dataset: str) -> dict | None:
        if output_format != "parquet":
            return None
        from focus_data_toolkit.io.parquet_io import dataset_metadata

        # The output is FOCUS 1.4; `version` is the *source* version (1.2/1.3) and belongs in
        # source_version, not target_version, so Parquet-metadata readers are not misled.
        return dataset_metadata(
            dataset,
            target_version="1.4",
            source_version=version,
            mode=mode.value,
            conformance="see manifest",
            tool_version=__version__,
        )

    reader = open_row_source(cost_and_usage)
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

    cc_rows = read_source_rows(contract_commitment) if contract_commitment else None
    source_cols = set(reader.source_columns)
    diagnostics: list[Diagnostic] = []

    # Supplements: a cheap pre-pass collects the source's join keys (memory bounded by
    # their distinct counts, i.e. supplement scale) so the bundle is fully validated
    # before anything is staged, and the strict back-link/provenance decisions are made
    # exactly as in the eager path.
    supp_keys: SourceKeySets | None = None
    linked = synthetic
    line_table = supplements.get("invoice_line") if supplements else None
    if supplements:
        supp_keys = SourceKeySets()
        pre = open_row_source(cost_and_usage)
        try:
            pre_unit, pre_total = _progress_totals(pre, progress)
            _emit("READING", 0, pre_total, pre_unit, "collecting source keys", force=True)
            pre_count = 0
            for record in pre:
                supp_keys.observe_cau_row(record.values)
                pre_count += 1
                if pre_count % step == 0:
                    _check()
                    _resource_guard()
                    if progress is not None:
                        _emit("READING", _source_completed(pre, pre_unit, pre_count),
                              pre_total, pre_unit, "collecting source keys")
        finally:
            pre.close()
        for cc_row in cc_rows or ():
            supp_keys.observe_cc_row(cc_row)
        supp_diags = validate_supplements(supplements, supp_keys)
        diagnostics.extend(supp_diags)
        errors = [d for d in supp_diags if d.severity is Severity.ERROR]
        if errors:
            reader.close()
            raise ConversionError(
                f"{len(errors)} supplement validation error(s); first: "
                f"[{errors[0].code}] {errors[0].message}"
            )
        # Will Invoice Detail be produced? (Rules only — the same _flip_rules the apply
        # step uses, so this matches the post-pass provenance exactly.)
        invd_flipped = flip_enriched_rules(
            INVOICE_DETAIL_PROVENANCE, "Invoice Detail",
            [t for t in (supplements.get("invoice"), line_table) if t is not None],
            supp_keys,
        )
        invd_blocked = bool(
            strict_blockers(invd_flipped, load_model()["datasets"]["Invoice Detail"]["columns"])
        )
        linked = bool(supp_keys.invoice_grains) and (synthetic or not invd_blocked)

    cu_prov = cost_and_usage_provenance(source_cols, version, invoice_detail_linked=linked)
    if supplements and supp_keys is not None and linked and line_table is not None:
        if "InvoiceDetailId" in line_table.fact_columns:
            id_cov = coverage(line_table, supp_keys.invoice_grains)["InvoiceDetailId"]
            if id_cov.complete:
                cu_prov["InvoiceDetailId"] = ColumnRule(
                    Lineage.ENRICHED,
                    line_table.source_for("InvoiceDetailId"),
                    note="issuer-assigned back-link to Invoice Detail",
                )
    provenance = {
        "Cost and Usage": cu_prov,
        "Contract Commitment": CONTRACT_COMMITMENT_PROVENANCE,
        "Billing Period": BILLING_PERIOD_PROVENANCE,
        "Invoice Detail": INVOICE_DETAIL_PROVENANCE,
    }
    row_counts = dict.fromkeys(load_model()["datasets"], 0)

    with ExitStack() as stack:
        out = stack.enter_context(
            AtomicOutputDir(out_dir, on_exists=on_exists, keep_temp=keep_temp)
        )
        # Relocated scratch (FOCUS_TOOLKIT_WORK_DIR) lives in a per-run subdirectory so concurrent
        # runs sharing one WORK_DIR never collide; it is outside the atomic staging dir, so we
        # remove it ourselves on every exit path.
        work_run = runtime.work_run_dir(config, out.run_id)

        def _cleanup_relocated_scratch() -> None:
            if work_run is not None:
                shutil.rmtree(work_run, ignore_errors=True)

        stack.callback(_cleanup_relocated_scratch)

        def _scratch_path(name: str) -> Path:
            """Scratch DB location: the per-run WORK_DIR subdir if set, else inside staging."""
            return (work_run / name) if work_run is not None else out.path_for(name)

        cu_columns = dataset_columns("Cost and Usage")
        cu_partition = partition_map.get("Cost and Usage")
        cu_file = _output_filename(
            "Cost and Usage", provenance, synthetic, output_format, partitioned=bool(cu_partition)
        )
        cu_handle, cu_writer = _open_writer(
            out.path_for(cu_file),
            DatasetSchema("Cost and Usage", cu_columns),
            output_format,
            metadata=_meta("Cost and Usage"),
            compression=compression,
            partition_by=cu_partition,
            target_file_size=target_file_size,
        )
        index_db_path = _scratch_path(_INDEX_DB)
        scratch_paths.append(index_db_path)  # tracked for the work budget in both configs
        index = (
            ExternalIndexOpener(index_db_path)
            if (synthetic or supplements)
            else None
        )
        if index is not None:
            # Guarantee the SQLite handle is closed before staging / work-dir cleanup on EVERY
            # exit path (a mid-run cancel or budget abort would otherwise leave it open, and
            # Windows cannot remove an open file). close() is idempotent, so the explicit close
            # on the success path is harmless.
            stack.callback(index.close)

        provider_seen: dict[tuple[str, str], ProviderContext] = {}
        billing_seen: dict[tuple, BillingContext] = {}
        cu_counters = LineageCounters()
        cu_count = 0

        tr_unit, tr_total = _progress_totals(reader, progress)
        _emit("TRANSFORMING", 0, tr_total, tr_unit, "converting Cost and Usage", force=True)
        try:
            for record in reader:
                row = record.values
                pctx = provider_context_of_row(row, version)
                provider_seen[(pctx.service_provider_name, pctx.host_provider_name)] = pctx
                bctx = billing_context_of_row(row)
                billing_seen[astuple(bctx)] = bctx

                grain = invoice_detail_grain_key(row)
                detail_id = ""
                if grain[1] and linked:
                    real_id = (
                        line_table.value(grain, "InvoiceDetailId")
                        if line_table is not None
                        else ""
                    )
                    if real_id:
                        detail_id = real_id
                    elif synthetic:
                        detail_id = invoice_detail_id(grain)
                cu_writer.write(
                    convert_cost_and_usage_row(
                        row, version, detail_id=detail_id, target=cu_columns,
                        counters=cu_counters,
                    )
                )
                cu_count += 1

                if index is not None:
                    if grain[1]:
                        index.stage_invoice_line(grain, (row.get("BilledCost") or "0"))
                    start = (row.get("BillingPeriodStart") or "").strip()
                    end = (row.get("BillingPeriodEnd") or "").strip()
                    issuer = (row.get("InvoiceIssuerName") or "").strip()
                    if start and end:
                        index.stage_billing_period(start, end, issuer)

                if cu_count % step == 0:
                    _check()
                    _resource_guard()
                    if progress is not None:
                        _emit("TRANSFORMING", _source_completed(reader, tr_unit, cu_count),
                              tr_total, tr_unit, "converting Cost and Usage")
        finally:
            cu_handle.close()
            reader.close()
        if not cu_count:
            # Header-only input: match the eager path rather than publishing a manifest-only
            # directory. Raising inside the context removes the staging dir (nothing published).
            raise ConversionError("no Cost and Usage rows to convert")
        row_counts["Cost and Usage"] = cu_count
        # Enforce budgets once the aggregation scratch is fully written — covers inputs shorter
        # than `step` and any growth after the last in-loop check.
        _resource_guard()
        if cu_partition is not None:
            diagnostics.extend(_partition_diagnostics(cu_partition, cu_writer.partition_count()))

        staged = {"Cost and Usage": cu_file}
        lineage_counts: dict[str, LineageCounters] = {"Cost and Usage": cu_counters}

        if index is not None:
            # Invoice Detail: rows come out of the SQLite aggregation (supplement-scale
            # cardinality). With supplements they are materialized and pushed through the
            # same apply function as the eager path, so both stay byte-identical.
            emitted = emitted_invoice_detail_columns()
            _emit("AGGREGATING", 0, None, "rows", "aggregating Invoice Detail", force=True)
            invd_rows = []
            for j, (grain, grp_total) in enumerate(index.finalize_invoice_groups(), 1):
                invd_rows.append(
                    invoice_detail_row(grain, grp_total, invoice_detail_id(grain), emitted)
                )
                if j % step == 0:
                    _check()
                    _emit("AGGREGATING", j, None, "rows", "aggregating Invoice Detail")
            if supplements and supp_keys is not None and invd_rows:
                applied, _mapping = apply_invoice_details(
                    invd_rows, {}, supplements, supp_keys, INVOICE_DETAIL_PROVENANCE,
                    synthetic=synthetic,
                )
                invd_rows = applied.rows or []
                provenance["Invoice Detail"] = applied.provenance
                lineage_counts["Invoice Detail"] = applied.counters
            id_columns = tuple(invd_rows[0].keys()) if invd_rows else tuple(emitted)
            id_file = _output_filename("Invoice Detail", provenance, synthetic, output_format)
            id_handle, id_writer = _open_writer(
                out.path_for(id_file),
                DatasetSchema("Invoice Detail", id_columns),
                output_format,
                metadata=_meta("Invoice Detail"),
                compression=compression,
            )
            _emit("WRITING", 0, len(invd_rows), "rows", "writing Invoice Detail", force=True)
            for j, invd_row in enumerate(invd_rows, 1):
                id_writer.write(invd_row)
                if j % step == 0:
                    _check()
                    _emit("WRITING", j, len(invd_rows), "rows", "writing Invoice Detail")
            id_handle.close()
            staged["Invoice Detail"] = id_file
            row_counts["Invoice Detail"] = len(invd_rows)

            bp_columns = dataset_columns("Billing Period")
            _emit("AGGREGATING", 0, None, "rows", "aggregating Billing Period", force=True)
            bp_rows = []
            for j, (bp_start, bp_end, bp_issuer) in enumerate(index.finalize_billing_periods(), 1):
                bp_rows.append(billing_period_row(bp_start, bp_end, bp_issuer, bp_columns))
                if j % step == 0:
                    _check()
                    _emit("AGGREGATING", j, None, "rows", "aggregating Billing Period")
            if supplements and supp_keys is not None and bp_rows:
                bp_applied = apply_billing_periods(
                    bp_rows, supplements, supp_keys, BILLING_PERIOD_PROVENANCE,
                    synthetic=synthetic,
                )
                bp_rows = bp_applied.rows or []
                provenance["Billing Period"] = bp_applied.provenance
                lineage_counts["Billing Period"] = bp_applied.counters
            bp_file = _output_filename("Billing Period", provenance, synthetic, output_format)
            bp_handle, bp_writer = _open_writer(
                out.path_for(bp_file),
                DatasetSchema("Billing Period", bp_columns),
                output_format,
                metadata=_meta("Billing Period"),
                compression=compression,
            )
            _emit("WRITING", 0, len(bp_rows), "rows", "writing Billing Period", force=True)
            for j, bp_row in enumerate(bp_rows, 1):
                bp_writer.write(bp_row)
                if j % step == 0:
                    _check()
                    _emit("WRITING", j, len(bp_rows), "rows", "writing Billing Period")
            bp_handle.close()
            staged["Billing Period"] = bp_file
            row_counts["Billing Period"] = len(bp_rows)

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
                    diagnostics=diagnostics,
                )
                if supplements and supp_keys is not None and cc_out:
                    cc_applied = apply_contract_commitments(
                        cc_out, supplements, supp_keys, CONTRACT_COMMITMENT_PROVENANCE,
                        synthetic=synthetic,
                    )
                    cc_out = cc_applied.rows or []
                    provenance["Contract Commitment"] = cc_applied.provenance
                    lineage_counts["Contract Commitment"] = cc_applied.counters
                cc_columns = dataset_columns("Contract Commitment")
                cc_file = _output_filename(
                    "Contract Commitment", provenance, synthetic, output_format
                )
                cc_handle, cc_writer = _open_writer(
                    out.path_for(cc_file),
                    DatasetSchema("Contract Commitment", cc_columns),
                    output_format,
                    metadata=_meta("Contract Commitment"),
                    compression=compression,
                )
                _emit("WRITING", 0, len(cc_out), "rows", "writing Contract Commitment", force=True)
                for j, r in enumerate(cc_out, 1):
                    cc_writer.write(r)
                    if j % step == 0:
                        _check()
                        _emit("WRITING", j, len(cc_out), "rows", "writing Contract Commitment")
                cc_handle.close()
                staged["Contract Commitment"] = cc_file
                row_counts["Contract Commitment"] = len(cc_out)
                diagnostics.extend(_context_diagnostics(provider_ambiguous, provider_ctx, issuers))

        if index is not None:
            index.close()
            _discard_scratch(out, config, _INDEX_DB, index_db_path)

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
            output_format=output_format,
            partitioned_by={k: list(v) for k, v in partition_map.items()},
            lineage_counts=lineage_counts,
            capabilities=capabilities,
            supplements=supplements.manifest_entries() if supplements else None,
        )

        # Remove any staged file whose dataset turned out NOT produced (e.g. zero derivable rows).
        for name, fname in staged.items():
            if name not in produced_output_files:
                target = out.path_for(fname)
                if name in partition_map and target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)

        # Mandatory validation gate (chunked, bounded memory): never publish a lint failure.
        if validate:
            for name, fname in produced_output_files.items():
                _check()
                dataset_total = row_counts.get(name) or 0
                _emit("VALIDATING", 0, dataset_total, "rows", f"linting {name}", force=True)

                def _lint_rows(done: int, _name: str = name, _total: int = dataset_total) -> None:
                    _emit("VALIDATING", done, _total, "rows", f"linting {_name}")

                report = _lint_file(
                    name, out.path_for(fname), output_format,
                    partition_by=partition_map.get(name), capabilities=capabilities,
                    check=_check, on_rows=_lint_rows,
                )
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

        # Cross-dataset publication gate: re-read the staged files (bounded memory — each
        # check is an independent forward pass, per-key state spills to a scratch SQLite DB
        # past a threshold) and refuse to publish on any ERROR. The outcome — or the
        # explicit skip — lands in the manifest, exactly as in the eager path.
        if validate:
            from focus_data_toolkit.storage.spill import SpillableIndexPool
            from focus_data_toolkit.validate.bundle import validate_dataset_bundle

            _check()
            _emit("VALIDATING", 0, None, "rows", "cross-dataset bundle validation", force=True)
            bundle_rows = {
                name: _StagedRows(
                    out.path_for(fname), output_format, name,
                    partition_by=partition_map.get(name), check=_check, guard=_resource_guard,
                )
                for name, fname in produced_output_files.items()
            }
            spill_db_path = _scratch_path(_BUNDLE_DB)
            scratch_paths.append(spill_db_path)  # tracked for the work budget in both configs
            spill = SpillableIndexPool(spill_db_path)
            stack.callback(spill.close)  # backstop: close before cleanup on any exit (Windows)
            try:
                bundle_report = validate_dataset_bundle(
                    bundle_rows, index_factory=spill.make_map
                )
            finally:
                spill.close()
            # Catch a spill DB that grew past the budget before it is discarded.
            _resource_guard()
            _discard_scratch(out, config, _BUNDLE_DB, spill_db_path)
            manifest["bundle_validation"] = bundle_report.as_dict()
            if not bundle_report.ok:
                first = bundle_report.errors[0]
                raise AtomicWriteError(
                    f"bundle validation failed ({len(bundle_report.errors)} error(s); "
                    f"first: [{first.code}] {first.message}); final output not written "
                    f"to {out_dir}"
                )
        else:
            manifest["bundle_validation"] = {"skipped": True}

        # Enroll produced files for fsync + checksums: a partitioned dataset is a tree of parts.
        for name, fname in produced_output_files.items():
            if name in partition_map:
                out.add_data_tree(fname)
            else:
                out.add_data_file(fname)

        # Last cancel + budget check before the atomic publish; the rename itself is fast and is
        # intentionally not interrupted (interrupting it would defeat atomicity).
        _check()
        _resource_guard()
        _emit("PUBLISHING", 0, None, "rows", "writing checksums + manifest", force=True)
        checksums = out.checksums()
        manifest_bytes = manifest_mod.render(manifest).encode("utf-8")
        sidecar = _run_metadata(
            produced_output_files,
            row_counts,
            manifest,
            out.run_id,
            __version__,
            generated_at,
            mode,
            output_format,
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


def _partition_diagnostics(partition_by: Sequence[str], count: int) -> list[Diagnostic]:
    from focus_data_toolkit.io.parquet_io import PARTITION_WARN_THRESHOLD

    if count <= PARTITION_WARN_THRESHOLD:
        return []
    return [
        Diagnostic(
            code="FDT-IO-004",
            severity=Severity.WARNING,
            message=f"--partition-by {list(partition_by)} produced {count} partitions; a "
            "high-cardinality key creates many small files — prefer a lower-cardinality key",
            datasets=("Cost and Usage",),
            context={"partitions": str(count)},
        )
    ]


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
    output_format: str = "csv",
) -> dict:
    files = []
    for name, filename in sorted(produced_output_files.items(), key=lambda kv: kv[1]):
        entry = manifest["datasets"].get(name, {})
        files.append(
            {
                "name": filename,
                "dataset": name,
                "format": output_format,
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
