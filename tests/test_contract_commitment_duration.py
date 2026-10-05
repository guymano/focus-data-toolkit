"""ContractCommitmentDurationType: derived only from whole calendar months, else supplied.

FOCUS 1.4 defines the duration as the standard offering length ("1 Year", "36 Months"...)
and says it MAY differ from the actual commitment period, so the converter derives it
only when the dates prove it; a supplement supplies it otherwise and always wins.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from focus_data_toolkit.convert import convert_to_focus_1_4
from focus_data_toolkit.convert.contract_commitment import convert_contract_commitment
from focus_data_toolkit.modes import Mode
from focus_data_toolkit.supplement import SupplementBundle, SupplementFileSpec
from focus_data_toolkit.validate.codes import CATALOG

DURATION = "ContractCommitmentDurationType"


def _duration(start: str, end: str) -> tuple[str, list]:
    diagnostics: list = []
    [row] = convert_contract_commitment(
        [{"ContractCommitmentId": "cc-1", "ContractCommitmentPeriodStart": start,
          "ContractCommitmentPeriodEnd": end}],
        service_provider_name="AWS", invoice_issuer_name="AWS", diagnostics=diagnostics,
    )
    return row[DURATION], diagnostics


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2026-05-01T05:00:00Z", "2027-05-01T05:00:00Z", "12 Months"),
        ("2026-05-01T00:00:00Z", "2029-05-01T00:00:00Z", "36 Months"),
        ("2026-05-01T00:00:00Z", "2026-06-01T00:00:00Z", "1 Month"),
        # Calendar month arithmetic clamps the day: Jan 31 + 1 month = Feb 28 (2026).
        ("2026-01-31T00:00:00Z", "2026-02-28T00:00:00Z", "1 Month"),
    ],
)
def test_whole_calendar_months_are_derived(start, end, expected):
    assert _duration(start, end) == (expected, [])


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-05-01T00:00:00Z", "2027-04-30T23:59:59Z"),  # one second short of a year
        ("2026-05-01T00:00:00Z", "2027-05-15T00:00:00Z"),  # 12.5 months: never rounded
        ("2026-05-01T00:00:00Z", "2026-05-20T00:00:00Z"),  # under a month
        ("2026-05-01T00:00:00Z", "2026-05-01T00:00:00Z"),  # empty period
        ("not a date", "2027-05-01T00:00:00Z"),
    ],
)
def test_other_spans_are_not_derived(start, end):
    duration, diagnostics = _duration(start, end)
    assert duration == ""
    [diag] = diagnostics
    assert diag.code == "FDT-CC-001" and diag.code in CATALOG


def _write(path: Path, rows: list[dict[str, str]]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_supplied_duration_fills_the_gap_and_wins_over_derivation(tmp_path, source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    cc = [dict(r) for r in cc]
    odd, regular = cc[0], cc[1]
    odd["ContractCommitmentPeriodEnd"] = "2027-05-15T00:00:00Z"  # not derivable
    supplement = _write(tmp_path / "terms.csv", [
        {"ContractCommitmentId": odd["ContractCommitmentId"], DURATION: "1 Year"},
        {"ContractCommitmentId": regular["ContractCommitmentId"], DURATION: "1 Year"},
    ])
    bundle = SupplementBundle.load([SupplementFileSpec(path=supplement)])
    result = convert_to_focus_1_4(cau, cc, mode=Mode.SYNTHETIC, supplements=bundle)
    by_id = {r["ContractCommitmentId"]: r for r in result.datasets["Contract Commitment"]}
    assert by_id[odd["ContractCommitmentId"]][DURATION] == "1 Year"
    assert by_id[regular["ContractCommitmentId"]][DURATION] == "1 Year"
