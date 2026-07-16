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
import io
from dataclasses import dataclass, field
from pathlib import Path

from focus_data_toolkit import manifest as manifest_mod
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
from focus_data_toolkit.model import FOCUS_1_4_DATASETS, load_model
from focus_data_toolkit.model.validator import LintReport, lint_focus_1_4_structure
from focus_data_toolkit.modes import Mode
from focus_data_toolkit.provenance import ColumnRule, has_assumptions, strict_blockers

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


def _provider_context(cau_rows: list[dict[str, str]], source_version: str) -> tuple[str, str]:
    """Return ``(service_provider_name, invoice_issuer_name)`` from the source."""
    first = cau_rows[0]
    if source_version == "1.3":
        service_provider = first.get("ServiceProviderName") or first.get("ProviderName", "")
    else:
        service_provider = first.get("ProviderName", "")
    issuer = first.get("InvoiceIssuerName") or service_provider
    return service_provider, issuer


def convert_to_focus_1_4(
    cau_rows: list[dict[str, str]],
    cc_rows: list[dict[str, str]] | None = None,
    *,
    source_version: str | None = None,
    mode: Mode | str = Mode.STRICT,
    validate: bool = True,
) -> ConversionResult:
    """Convert FOCUS 1.2/1.3 rows into the FOCUS 1.4 datasets for the given ``mode``.

    ``cau_rows`` is a FOCUS 1.2 or 1.3 Cost and Usage table; ``cc_rows`` is the optional
    FOCUS 1.3 Contract Commitment table. Returns a :class:`ConversionResult` carrying the
    produced datasets, per-column provenance, a manifest and (when ``validate``) lint
    reports for the produced datasets.
    """
    if not cau_rows:
        raise ConversionError("no Cost and Usage rows to convert")
    mode = Mode(mode)
    version = source_version or detect_focus_version(cau_rows[0].keys())
    if version not in ("1.2", "1.3"):
        raise ConversionError(f"unsupported source version {version!r}")
    synthetic = mode is Mode.SYNTHETIC
    service_provider, issuer = _provider_context(cau_rows, version)
    model = load_model()
    source_cols = set(cau_rows[0].keys())

    # Synthetic-only builders (Billing Period / Invoice Detail / Contract Commitment are
    # never strictly producible from a Cost-and-Usage source).
    if synthetic:
        invoice_rows, id_mapping = build_invoice_details(cau_rows, invoice_issuer_name=issuer)
        billing_rows = build_billing_periods(cau_rows, invoice_issuer_name=issuer)
        commitment_rows = (
            convert_contract_commitment(
                cc_rows, service_provider_name=service_provider, invoice_issuer_name=issuer
            )
            if cc_rows
            else None
        )
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
    )

    result = ConversionResult(
        source_version=version,
        mode=mode,
        datasets=produced,
        provenance=provenance,
        manifest=manifest,
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


def write_result(result: ConversionResult, out_dir: str | Path) -> list[Path]:
    """Write every produced dataset plus the manifest to ``out_dir``; return the paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, rows in result.datasets.items():
        path = out / result.output_filename(name)
        path.write_bytes(rows_to_csv_bytes(rows))
        written.append(path)
    manifest_path = out / manifest_mod.MANIFEST_FILENAME
    manifest_path.write_text(manifest_mod.render(result.manifest), encoding="utf-8")
    written.append(manifest_path)
    return written


__all__ = [
    "DATASET_FILENAMES",
    "ConversionError",
    "ConversionResult",
    "convert_to_focus_1_4",
    "detect_focus_version",
    "read_csv_rows",
    "rows_to_csv_bytes",
    "write_result",
]
