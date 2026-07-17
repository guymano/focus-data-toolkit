"""Contract Commitment expansion semantics (P0 correctness fixes).

* The synthetic ``ContractCommitmentApplicability`` object must satisfy the official
  object schema: without a scope flag, ``Inclusions``/``InclusionOperator`` are
  required, so the minimal conformant synthetic object declares ``IsComplexScope``.
* An unparseable commitment period must never yield a fabricated ``"12 Months"``
  duration: the value stays empty and an ``FDT-CC-001`` WARNING reports the rows.
"""

from __future__ import annotations

import json

from focus_data_toolkit.convert.contract_commitment import (
    _APPLICABILITY,
    _duration_type,
    convert_contract_commitment,
)
from focus_data_toolkit.errors import Diagnostic, Severity


def cc_row(**over: str) -> dict[str, str]:
    base = {
        "BillingCurrency": "USD",
        "ContractCommitmentCategory": "Spend",
        "ContractCommitmentCost": "1000.00",
        "ContractCommitmentDescription": "test commitment",
        "ContractCommitmentId": "CC-1",
        "ContractCommitmentPeriodEnd": "2027-05-01T00:00:00Z",
        "ContractCommitmentPeriodStart": "2026-05-01T00:00:00Z",
        "ContractCommitmentQuantity": "",
        "ContractCommitmentType": "Committed Spend",
        "ContractCommitmentUnit": "USD",
        "ContractId": "C-1",
        "ContractPeriodEnd": "2027-05-01T00:00:00Z",
        "ContractPeriodStart": "2026-05-01T00:00:00Z",
    }
    base.update(over)
    return base


def _convert(rows: list[dict[str, str]], diagnostics: list[Diagnostic] | None = None):
    return convert_contract_commitment(
        rows,
        service_provider_name="AWS",
        invoice_issuer_name="AWS",
        diagnostics=diagnostics,
    )


def test_synthetic_applicability_declares_a_scope():
    obj = json.loads(_APPLICABILITY)
    # Official schema: when neither IsGlobalScope nor IsComplexScope is true,
    # Inclusions + InclusionOperator become required. The synthetic object cannot
    # know the real terms, so it must declare a complex scope.
    assert obj["IsComplexScope"] is True
    assert all(k == "IsComplexScope" or k.startswith("x_") for k in obj)


def test_duration_type_from_valid_periods():
    assert _duration_type("2026-05-01T00:00:00Z", "2026-06-01T00:00:00Z") == "1 Month"
    assert _duration_type("2026-05-01T00:00:00Z", "2027-05-01T00:00:00Z") == "12 Months"
    assert _duration_type("2026-05-01T00:00:00Z", "2029-05-01T00:00:00Z") == "36 Months"


def test_duration_type_never_fabricated():
    assert _duration_type("not-a-date", "2027-05-01T00:00:00Z") == ""
    assert _duration_type("", "") == ""
    # Inverted period: end before start is not derivable either.
    assert _duration_type("2027-05-01T00:00:00Z", "2026-05-01T00:00:00Z") == ""


def test_unparseable_period_reports_fdt_cc_001():
    diagnostics: list[Diagnostic] = []
    out = _convert(
        [cc_row(), cc_row(ContractCommitmentId="CC-2", ContractCommitmentPeriodStart="garbage")],
        diagnostics,
    )
    assert out[0]["ContractCommitmentDurationType"] == "12 Months"
    assert out[1]["ContractCommitmentDurationType"] == ""
    assert len(diagnostics) == 1
    diag = diagnostics[0]
    assert diag.code == "FDT-CC-001"
    assert diag.severity is Severity.WARNING
    assert diag.context["row_count"] == "1"
    assert diag.context["contract_commitment_ids"] == "CC-2"


def test_valid_periods_produce_no_diagnostic():
    diagnostics: list[Diagnostic] = []
    _convert([cc_row()], diagnostics)
    assert diagnostics == []


def test_converted_applicability_is_the_scoped_object():
    out = _convert([cc_row()])
    obj = json.loads(out[0]["ContractCommitmentApplicability"])
    assert obj["IsComplexScope"] is True
