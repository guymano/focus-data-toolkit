"""Commitment lifecycle and correction-integrity checks.

Phase A covers the cheap, spec-grounded temporal and percentage-range checks on Contract
Commitment, plus a forward-compatible correction-reference check: a Cost and Usage correction
line (``ChargeClass="Correction"``) may point at the charge it corrects via the toolkit
extension ``x_CorrectionOf`` -> an original row's ``x_ChargeKey``; the referenced original must
still be present (corrections never silently overwrite history). Deeper correction scenarios
arrive with the Phase B generators.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation

from focus_data_toolkit.errors import Diagnostic, Severity

Rows = Sequence[Mapping[str, str]]
#: Inputs are consumed in forward passes only, so any (re-)iterable of rows works.
RowStream = Iterable[Mapping[str, str]]
#: Factory for per-key lookup state (``dict`` by default; a spillable map for streaming).
IndexFactory = Callable[[], MutableMapping[str, str]]

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
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    # Require a timezone (FOCUS Date/Time is UTC 'Z'). A naive value is malformed for this
    # lifecycle check; returning None avoids a naive-vs-aware TypeError on comparison and
    # lets the per-dataset linter report the bad Date/Time format.
    return parsed if parsed.tzinfo is not None else None


def check_contract_commitment_periods(contract_commitment: RowStream) -> list[Diagnostic]:
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


def check_contract_commitment_percentages(contract_commitment: RowStream) -> list[Diagnostic]:
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
            if not value.is_finite():
                continue  # NaN/Infinity is a format issue for the linter, not a range error
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


def _is_correction(row: Mapping[str, str]) -> bool:
    return (row.get("ChargeClass") or "").strip().casefold() == "correction"


def _dec(value: str | None) -> Decimal | None:
    try:
        parsed = Decimal((value or "").strip())
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def check_no_duplicate_charge_keys(
    cost_and_usage: RowStream, *, index_factory: IndexFactory = dict
) -> list[Diagnostic]:
    """``x_ChargeKey`` must be unique: reusing one silently overwrites an auditable line.

    Corrections are appended as *new* keyed rows (``x_ChargeKey`` + ``x_CorrectionOf``), so a
    repeated key means an original (or a prior correction) was overwritten rather than amended.
    """
    seen = index_factory()
    out: list[Diagnostic] = []
    for i, row in enumerate(cost_and_usage, start=1):
        key = (row.get("x_ChargeKey") or "").strip()
        if not key:
            continue
        if key in seen:
            out.append(
                Diagnostic(
                    code="FDT-CORR-003",
                    severity=Severity.ERROR,
                    message=f"x_ChargeKey {key!r} appears on rows {seen[key]} and {i} — a "
                    "correction must add a new keyed row, never overwrite an original",
                    datasets=("Cost and Usage",),
                    dataset="Cost and Usage",
                    line_number=i,
                    column="x_ChargeKey",
                    value=key,
                    record_keys={"x_ChargeKey": key},
                )
            )
        else:
            seen[key] = str(i)
    return out


def check_correction_net_sums(
    cost_and_usage: RowStream,
    *,
    tolerance: Decimal = Decimal("0.01"),
    index_factory: IndexFactory = dict,
) -> list[Diagnostic]:
    """The net of a correction set must equal the declared ``x_NetCharge``.

    A correction set is an original charge (``x_ChargeKey`` == the corrections'
    ``x_CorrectionOf``) plus every correction pointing at it. When a correction declares the
    post-correction net in ``x_NetCharge``, the arithmetic sum of ``BilledCost`` over the set up
    to and including that correction must match it — so the running net stays auditable and no
    money is silently created or lost. Sets whose original is absent are handled by
    :func:`check_correction_references`; this check only reconciles what is present.
    """
    # Decimals are stored as exact strings so the lookup state can live in a spillable map.
    originals = index_factory()
    for row in cost_and_usage:
        if _is_correction(row):
            continue
        key = (row.get("x_ChargeKey") or "").strip()
        cost = _dec(row.get("BilledCost"))
        if key and cost is not None:
            originals[key] = str(cost)

    # Accumulate corrections per original in row order (running net is order-sensitive).
    running = index_factory()
    out: list[Diagnostic] = []
    for i, row in enumerate(cost_and_usage, start=1):
        if not _is_correction(row):
            continue
        ref = (row.get("x_CorrectionOf") or "").strip()
        if ref not in originals:
            continue  # missing original -> reported by check_correction_references
        delta = _dec(row.get("BilledCost"))
        if delta is None:
            continue
        base = running.get(ref) or originals[ref]
        net = Decimal(base) + delta
        running[ref] = str(net)
        declared = _dec(row.get("x_NetCharge"))
        if declared is None:
            continue
        if abs(net - declared) > tolerance:
            out.append(
                Diagnostic(
                    code="FDT-CORR-002",
                    severity=Severity.ERROR,
                    message=f"correction set for {ref!r} nets to {net} but x_NetCharge "
                    f"declares {declared}",
                    datasets=("Cost and Usage",),
                    dataset="Cost and Usage",
                    line_number=i,
                    column="x_NetCharge",
                    expected=str(declared),
                    actual=str(net),
                    record_keys={"x_CorrectionOf": ref},
                )
            )
    return out


def check_correction_references(
    cost_and_usage: RowStream, *, index_factory: IndexFactory = dict
) -> list[Diagnostic]:
    """A correction line's ``x_CorrectionOf`` must point at a still-present *original* charge.

    The lookup is built only from non-correction originals, and a correction that references
    its own key is rejected — otherwise a correction with ``x_ChargeKey == x_CorrectionOf`` and
    no surviving original would pass, defeating the auditability guarantee.
    """
    original_keys = index_factory()
    for r in cost_and_usage:
        key = (r.get("x_ChargeKey") or "").strip()
        if key and not _is_correction(r):
            original_keys[key] = ""
    out: list[Diagnostic] = []
    for i, row in enumerate(cost_and_usage, start=1):
        ref = (row.get("x_CorrectionOf") or "").strip()
        if not ref:
            continue
        self_key = (row.get("x_ChargeKey") or "").strip()
        if ref == self_key or ref not in original_keys:
            out.append(
                Diagnostic(
                    code="FDT-CORR-001",
                    severity=Severity.ERROR,
                    message=f"correction row {i} references original charge {ref!r} that is not "
                    "present as an original (original must remain auditable)",
                    datasets=("Cost and Usage",),
                    dataset="Cost and Usage",
                    line_number=i,
                    column="x_CorrectionOf",
                    value=ref,
                    record_keys={"x_CorrectionOf": ref},
                )
            )
    return out
