"""Convert FOCUS 1.2/1.3 source data into the four FOCUS 1.4 datasets."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from focus_data_toolkit.convert.billing_period import build_billing_periods
from focus_data_toolkit.convert.contract_commitment import convert_contract_commitment
from focus_data_toolkit.convert.cost_and_usage import convert_cost_and_usage
from focus_data_toolkit.convert.detect import detect_focus_version
from focus_data_toolkit.convert.invoice_detail import build_invoice_details
from focus_data_toolkit.model.validator import ValidationReport, validate_focus_1_4

# Output file name per dataset (stable, snake_case).
DATASET_FILENAMES = {
    "Cost and Usage": "focus_1_4_cost_and_usage.csv",
    "Contract Commitment": "focus_1_4_contract_commitment.csv",
    "Billing Period": "focus_1_4_billing_period.csv",
    "Invoice Detail": "focus_1_4_invoice_detail.csv",
}


class ConversionError(ValueError):
    """Raised when the source cannot be converted or the output is non-conformant."""


@dataclass
class ConversionResult:
    """Outcome of a 1.x -> 1.4 conversion."""

    source_version: str
    datasets: dict[str, list[dict[str, str]]]
    reports: dict[str, ValidationReport] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.reports.values())

    @property
    def coverage(self) -> tuple[str, ...]:
        """Datasets actually produced (FOCUS 1.4 'dataset coverage')."""
        return tuple(name for name, rows in self.datasets.items() if rows)


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
    validate: bool = True,
) -> ConversionResult:
    """Convert FOCUS 1.2/1.3 rows into the four FOCUS 1.4 datasets.

    ``cau_rows`` is a FOCUS 1.2 or 1.3 Cost and Usage table (list of dicts);
    ``cc_rows`` is the optional FOCUS 1.3 Contract Commitment table. The
    Billing Period and Invoice Detail datasets are derived from the Cost and
    Usage rows (they do not exist before 1.4). When ``validate`` is true every
    produced dataset is checked against the committed FOCUS 1.4 model and the
    reports are attached to the result.
    """
    if not cau_rows:
        raise ConversionError("no Cost and Usage rows to convert")
    version = source_version or detect_focus_version(cau_rows[0].keys())
    if version not in ("1.2", "1.3"):
        raise ConversionError(f"unsupported source version {version!r}")
    service_provider, issuer = _provider_context(cau_rows, version)

    invoice_details, id_mapping = build_invoice_details(cau_rows, invoice_issuer_name=issuer)
    datasets: dict[str, list[dict[str, str]]] = {
        "Cost and Usage": convert_cost_and_usage(
            cau_rows, version, invoice_detail_ids=id_mapping
        ),
        "Contract Commitment": (
            convert_contract_commitment(
                cc_rows, service_provider_name=service_provider, invoice_issuer_name=issuer
            )
            if cc_rows
            else []
        ),
        "Billing Period": build_billing_periods(cau_rows, invoice_issuer_name=issuer),
        "Invoice Detail": invoice_details,
    }

    result = ConversionResult(source_version=version, datasets=datasets)
    if validate:
        for name, rows in datasets.items():
            if rows:
                result.reports[name] = validate_focus_1_4(name, rows)
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
    """Write every non-empty dataset of ``result`` to ``out_dir``; return the paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, rows in result.datasets.items():
        if not rows:
            continue
        path = out / DATASET_FILENAMES[name]
        path.write_bytes(rows_to_csv_bytes(rows))
        written.append(path)
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
