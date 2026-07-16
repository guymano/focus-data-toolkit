"""Commitment lifecycle and correction-integrity checks.

Phase A covers the cheap, spec-grounded temporal and percentage-range checks on Contract
Commitment, plus a forward-compatible correction-reference check: a Cost and Usage correction
line (``ChargeClass="Correction"``) may point at the charge it corrects via the toolkit
extension ``x_CorrectionOf`` -> an original row's ``x_ChargeKey``; the referenced original must
still be present (corrections never silently overwrite history). Deeper correction scenarios
arrive with the Phase B generators.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation

from focus_data_toolkit.errors import Diagnostic, Severity

Rows = Sequence[Mapping[str, str]]

_PERCENTAGE_COLUMNS = (
    "ContractCommitmentDiscountPercentage",
    "ContractCommitmentPaymentUpfrontPercentage",
)
_PERIOD_PAIRS = (
    ("ContractCommitmentPeriodStart", "ContractCommitmentPeriodEnd"),
    ("ContractPeriodStart", "ContractPeriodEnd"),
)


def _parse_dt(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def check_contract_commitment_periods(contract_commitment: Rows) -> list[Diagnostic]:
    """Each commitment/contract period must start strictly before it ends."""
    out: list[Diagnostic] = []
    for i, row in enumerate(contract_commitment, start=1):
        for start_col, end_col in _PERIOD_PAIRS:
            start, end = _parse_dt(row.get(start_col, "")), _parse_dt(row.get(end_col, ""))
            if start is None or end is None or start < end:
                continue
            out.append(
                Diagnostic(
                    code="FDT-CROSS-050",
                    severity=Severity.ERROR,
                    message=f"{start_col} is not before {end_col}",
                    datasets=("Contract Commitment",),
                    dataset="Contract Commitment",
                    line_number=i,
                    column=start_col,
                    expected=f"< {row.get(end_col)}",
                    actual=row.get(start_col, ""),
                    record_keys={"ContractCommitmentId": (row.get("ContractCommitmentId") or "")},
                )
            )
    return out


def check_contract_commitment_percentages(contract_commitment: Rows) -> list[Diagnostic]:
    """Percentage columns must be within [0, 1]."""
    out: list[Diagnostic] = []
    for i, row in enumerate(contract_commitment, start=1):
        for col in _PERCENTAGE_COLUMNS:
            raw = (row.get(col) or "").strip()
            if not raw:
                continue
            try:
                value = Decimal(raw)
            except InvalidOperation:
                continue  # value-format is the per-dataset linter's job
            if value < 0 or value > 1:
                out.append(
                    Diagnostic(
                        code="FDT-CROSS-051",
                        severity=Severity.ERROR,
                        message=f"{col} value {raw} is outside [0, 1]",
                        datasets=("Contract Commitment",),
                        dataset="Contract Commitment",
                        line_number=i,
                        column=col,
                        actual=raw,
                        record_keys={"ContractCommitmentId": (row.get("ContractCommitmentId") or "")},
                    )
                )
    return out


def check_correction_references(cost_and_usage: Rows) -> list[Diagnostic]:
    """A correction line's ``x_CorrectionOf`` must point at a still-present original charge."""
    known_keys = {
        (r.get("x_ChargeKey") or "").strip()
        for r in cost_and_usage
        if (r.get("x_ChargeKey") or "").strip()
    }
    out: list[Diagnostic] = []
    for i, row in enumerate(cost_and_usage, start=1):
        ref = (row.get("x_CorrectionOf") or "").strip()
        if not ref:
            continue
        if ref not in known_keys:
            out.append(
                Diagnostic(
                    code="FDT-CORR-001",
                    severity=Severity.ERROR,
                    message=f"correction row {i} references original charge {ref!r} that is not "
                    "present (original must remain auditable)",
                    datasets=("Cost and Usage",),
                    dataset="Cost and Usage",
                    line_number=i,
                    column="x_CorrectionOf",
                    value=ref,
                    record_keys={"x_CorrectionOf": ref},
                )
            )
    return out
