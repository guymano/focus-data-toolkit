"""ContractCommitmentDurationType: derived only from whole calendar months, else supplied.

FOCUS 1.4 defines the duration as the standard offering length ("1 Year", "3 Years"...)
and says it MAY differ from the actual commitment period, so the converter derives it
only when the dates prove it. A supplement supplies it otherwise and always wins. Strict
mode never guesses (the dataset is not produced until the term is supplied); synthetic
mode assumes the nearest whole-month value and says so.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from focus_data_toolkit.convert import convert_files, convert_to_focus_1_4, write_result
from focus_data_toolkit.convert.contract_commitment import (
    PROVENANCE,
    convert_contract_commitment,
    format_duration,
    settle_duration_type,
)
from focus_data_toolkit.model.validator import check_column_value
from focus_data_toolkit.modes import Mode
from focus_data_toolkit.provenance import Lineage, LineageCounters
from focus_data_toolkit.supplement import (
    SupplementBundle,
    SupplementError,
    SupplementFileSpec,
    compute_gaps,
)
from focus_data_toolkit.validate.codes import CATALOG

DURATION = "ContractCommitmentDurationType"


def _convert(start: str, end: str, *, synthetic: bool = False) -> tuple[str, Lineage]:
    lineages: list[Lineage] = []
    [row] = convert_contract_commitment(
        [{"ContractCommitmentId": "cc-1", "ContractCommitmentPeriodStart": start,
          "ContractCommitmentPeriodEnd": end}],
        service_provider_name="AWS", invoice_issuer_name="AWS",
        synthetic=synthetic, duration_lineages=lineages,
    )
    [lineage] = lineages
    return row[DURATION], lineage


@pytest.mark.parametrize(
    ("months", "expected"),
    [(1, "1 Month"), (2, "2 Months"), (11, "11 Months"), (12, "1 Year"), (18, "18 Months"),
     (24, "2 Years"), (36, "3 Years"), (60, "5 Years")],
)
def test_format_uses_years_when_whole(months, expected):
    assert format_duration(months) == expected


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2026-05-01T05:00:00Z", "2027-05-01T05:00:00Z", "1 Year"),
        ("2026-05-01T00:00:00Z", "2029-05-01T00:00:00Z", "3 Years"),
        ("2026-05-01T00:00:00Z", "2026-06-01T00:00:00Z", "1 Month"),
        ("2026-05-01T00:00:00Z", "2027-11-01T00:00:00Z", "18 Months"),
        # Calendar month arithmetic clamps the day: Jan 31 + 1 month = Feb 28 (2026).
        ("2026-01-31T00:00:00Z", "2026-02-28T00:00:00Z", "1 Month"),
        # A leap day: Feb 29 + 12 months = Feb 28 of the next year.
        ("2028-02-29T00:00:00Z", "2029-02-28T00:00:00Z", "1 Year"),
        # A naive timestamp is UTC (FOCUS requires UTC), so it compares with a Z one.
        ("2026-05-01T00:00:00", "2027-05-01T00:00:00Z", "1 Year"),
        # Months are counted in the start's frame, whatever the end's offset.
        ("2026-05-01T00:30:00+01:00", "2027-04-30T23:30:00Z", "1 Year"),
    ],
)
def test_whole_calendar_months_are_derived(start, end, expected):
    assert _convert(start, end) == (expected, Lineage.DERIVED)
    assert _convert(start, end, synthetic=True) == (expected, Lineage.DERIVED)


@pytest.mark.parametrize(
    ("start", "end", "assumed"),
    [
        ("2026-05-01T00:00:00Z", "2027-04-30T23:59:59Z", "1 Year"),  # a second short
        ("2026-05-01T00:00:00Z", "2027-05-15T00:00:00Z", "1 Year"),  # 12.5 months
        ("2026-05-01T00:00:00Z", "2026-05-20T00:00:00Z", "1 Month"),  # under a month
        ("2028-01-01T00:00:00Z", "2028-12-31T00:00:00Z", "1 Year"),  # 365 days, leap year
    ],
)
def test_other_spans_are_unavailable_in_strict_and_assumed_in_synthetic(start, end, assumed):
    assert _convert(start, end) == ("", Lineage.UNAVAILABLE)
    assert _convert(start, end, synthetic=True) == (assumed, Lineage.ASSUMED)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-05-01T00:00:00Z", "2026-05-01T00:00:00Z"),  # empty period
        ("2027-05-01T00:00:00Z", "2026-05-01T00:00:00Z"),  # inverted
        ("not a date", "2027-05-01T00:00:00Z"),
        ("", ""),
    ],
)
def test_unusable_periods_are_never_assumed(start, end):
    assert _convert(start, end) == ("", Lineage.UNAVAILABLE)
    assert _convert(start, end, synthetic=True) == ("", Lineage.UNAVAILABLE)


def _rows(*periods: tuple[str, str]) -> list[dict[str, str]]:
    return [
        {"ContractCommitmentId": f"cc-{n}", "ContractCommitmentPeriodStart": start,
         "ContractCommitmentPeriodEnd": end}
        for n, (start, end) in enumerate(periods, 1)
    ]


WHOLE = ("2026-05-01T00:00:00Z", "2027-05-01T00:00:00Z")
ODD = ("2026-05-01T00:00:00Z", "2027-05-15T00:00:00Z")


def _settle(rows, *, synthetic: bool, table=None):
    lineages: list[Lineage] = []
    out = convert_contract_commitment(
        rows, service_provider_name="AWS", invoice_issuer_name="AWS",
        synthetic=synthetic, duration_lineages=lineages,
    )
    counters = LineageCounters()
    prov, diag = settle_duration_type(
        out, lineages, dict(PROVENANCE), table=table, synthetic=synthetic, counters=counters
    )
    return out, prov[DURATION], diag, counters.summary().get(DURATION, {})


def test_settle_strict_reports_and_blocks_only_unresolved_rows():
    out, rule, diag, counts = _settle(_rows(WHOLE, ODD, ODD), synthetic=False)
    assert [r[DURATION] for r in out] == ["1 Year", "", ""]
    assert rule.lineage is Lineage.UNAVAILABLE
    assert counts == {"DERIVED": 1, "UNAVAILABLE": 2}
    assert diag is not None and diag.code == "FDT-CC-001" and diag.code in CATALOG
    assert diag.column == DURATION
    assert diag.context == {"row_count": "2", "contract_commitment_ids": "cc-2, cc-3"}
    assert "strict mode" in diag.message


def test_settle_synthetic_assumes_and_says_so():
    out, rule, diag, counts = _settle(_rows(WHOLE, ODD), synthetic=True)
    assert [r[DURATION] for r in out] == ["1 Year", "1 Year"]
    assert rule.lineage is Lineage.ASSUMED
    assert counts == {"ASSUMED": 1, "DERIVED": 1}
    assert diag is not None and diag.context["contract_commitment_ids"] == "cc-2"
    assert "synthetic mode" in diag.message


def test_settle_all_derived_keeps_the_derived_rule_and_reports_nothing():
    out, rule, diag, counts = _settle(_rows(WHOLE, WHOLE), synthetic=False)
    assert rule == PROVENANCE[DURATION]
    assert diag is None
    assert counts == {"DERIVED": 2}


def _write(path: Path, rows: list[dict[str, str]]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _table(tmp_path: Path, values: dict[str, str]):
    path = _write(tmp_path / "terms.csv", [
        {"ContractCommitmentId": cc_id, DURATION: value} for cc_id, value in values.items()
    ])
    return SupplementBundle.load([SupplementFileSpec(path=path)]).get("contract_commitment")


def test_settle_supplied_value_wins_and_is_counted_once(tmp_path):
    table = _table(tmp_path, {"cc-1": "3 Years", "cc-2": "1 Year"})
    out, rule, diag, counts = _settle(_rows(WHOLE, ODD), synthetic=False, table=table)
    assert [r[DURATION] for r in out] == ["3 Years", "1 Year"]
    assert rule.lineage is Lineage.ENRICHED and rule.source is not None
    assert counts == {"ENRICHED": 2}
    assert diag is None


def test_settle_partial_supplement_keeps_derived_rows(tmp_path):
    table = _table(tmp_path, {"cc-2": "1 Year"})
    out, rule, diag, counts = _settle(_rows(WHOLE, ODD), synthetic=False, table=table)
    assert [r[DURATION] for r in out] == ["1 Year", "1 Year"]
    assert rule.lineage is Lineage.DERIVED
    assert counts == {"DERIVED": 1, "ENRICHED": 1}
    assert diag is None


def test_supplied_duration_must_follow_the_expected_format(tmp_path, source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    path = _write(tmp_path / "terms.csv", [
        {"ContractCommitmentId": cc[0]["ContractCommitmentId"], DURATION: "12 months"},
    ])
    bundle = SupplementBundle.load([SupplementFileSpec(path=path)])
    with pytest.raises(Exception, match="FDT-SUPP-004"):
        convert_to_focus_1_4(cau, cc, mode=Mode.SYNTHETIC, supplements=bundle)


def _commitment_terms(cc) -> list[dict[str, str]]:
    applicability = json.dumps({"IsComplexScope": True, "x_Source": "contract records"},
                               separators=(",", ":"))
    return [
        {"ContractCommitmentId": r["ContractCommitmentId"],
         "ContractCommitmentCreated": r["ContractCommitmentPeriodStart"],
         "ContractCommitmentLastUpdated": r["ContractCommitmentPeriodStart"],
         "ContractCommitmentApplicability": applicability,
         "ContractCommitmentBenefitCategory": "Discount",
         "ContractCommitmentFulfillmentInterval": "Monthly",
         "ContractCommitmentLifecycleStatus": "Active",
         "ContractCommitmentModel": "Continuous",
         "ContractCommitmentOfferCategory": "Public",
         "ContractCommitmentPaymentInterval": "Monthly",
         "ContractCommitmentPaymentModel": "No Upfront",
         DURATION: ""}
        for r in cc
    ]


@pytest.fixture
def odd_source(source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    cc = [dict(r) for r in cc]
    cc[0]["ContractCommitmentPeriodEnd"] = "2027-05-15T00:00:00Z"  # not derivable
    return cau, cc


def test_strict_dataset_waits_for_the_missing_term(tmp_path, odd_source):
    cau, cc = odd_source
    bundle = SupplementBundle.load(
        [SupplementFileSpec(path=_write(tmp_path / "cc.csv", _commitment_terms(cc)))]
    )
    result = convert_to_focus_1_4(cau, cc, mode=Mode.STRICT, supplements=bundle)
    entry = result.manifest["datasets"]["Contract Commitment"]
    assert entry["status"] == "NOT_PRODUCED"
    assert entry["columns"][DURATION]["lineage"] == "UNAVAILABLE"
    assert [d for d in result.diagnostics if d.code == "FDT-CC-001"]
    # The other datasets are unaffected.
    assert result.manifest["datasets"]["Cost and Usage"]["status"] == "PRODUCED"


def test_strict_dataset_is_produced_once_the_term_is_supplied(tmp_path, odd_source):
    cau, cc = odd_source
    terms = _commitment_terms(cc)
    terms[0][DURATION] = "1 Year"  # only the row the dates cannot prove
    bundle = SupplementBundle.load([SupplementFileSpec(path=_write(tmp_path / "cc.csv", terms))])
    result = convert_to_focus_1_4(cau, cc, mode=Mode.STRICT, supplements=bundle)
    entry = result.manifest["datasets"]["Contract Commitment"]
    assert entry["status"] == "PRODUCED", entry
    rows = result.datasets["Contract Commitment"]
    assert all(r[DURATION] for r in rows)  # derived rows were not blanked
    summary = entry["lineage_summary"][DURATION]
    assert summary == {"DERIVED": len(cc) - 1, "ENRICHED": 1}
    assert not [d for d in result.diagnostics if d.code == "FDT-CC-001"]


@pytest.mark.parametrize("mode", ["strict", "synthetic"])
def test_streaming_matches_eager(tmp_path, odd_source, mode):
    cau, cc = odd_source
    terms = _commitment_terms(cc)
    terms[1][DURATION] = "3 Years"  # a supplied value over a derivable row
    bundle = SupplementBundle.load([SupplementFileSpec(path=_write(tmp_path / "t.csv", terms))])
    cau_path = _write(tmp_path / "cau.csv", cau)
    cc_path = _write(tmp_path / "cc_source.csv", cc)
    ref, streamed = tmp_path / "ref", tmp_path / "streamed"
    eager = convert_to_focus_1_4(cau, cc, mode=mode, supplements=bundle)
    assert "FDT-CC-001" in {d.code for d in eager.diagnostics}
    write_result(eager, ref)
    convert_files(cau_path, streamed, contract_commitment=cc_path, mode=mode, supplements=bundle)
    ref_manifest = json.loads((ref / "focus_1_4_manifest.json").read_text())
    assert ref_manifest == json.loads((streamed / "focus_1_4_manifest.json").read_text())
    for name in sorted(p.name for p in ref.iterdir() if p.suffix == ".csv"):
        assert (ref / name).read_bytes() == (streamed / name).read_bytes(), name


def test_aws_v1_export_names_the_missing_arn(tmp_path):
    path = tmp_path / "savings_plans.json"
    path.write_text(json.dumps({"savingsPlans": [{
        "savingsPlanId": "a1b2c3d4-0000-0000-0000-000000000001",
        "paymentOption": "No Upfront", "state": "active",
    }]}), encoding="utf-8")
    with pytest.raises(SupplementError, match=r"aws-savings-plans@2 requires savingsPlanArn"):
        SupplementBundle.load([SupplementFileSpec(path=path)])


@pytest.mark.parametrize(
    "value", ["1 Year", "3 Years", "36 Months", "1 Month", "90 Days", "2 Quarters", "1 Week",
              "12 Hours", "30 Minutes"],
)
def test_expected_format_accepts_number_and_listed_unit(value):
    assert check_column_value("Contract Commitment", DURATION, value) is None


@pytest.mark.parametrize(
    "value", ["12 months", "1year", "1  Year", "0 Years", "01 Year", "1.5 Years", "-1 Year",
              "1 Decade", "Year 1", "P1Y"],
)
def test_expected_format_rejects_anything_else(value):
    assert check_column_value("Contract Commitment", DURATION, value) == "bad_expected_format"


CC_1_3 = (
    "BillingCurrency", "ContractCommitmentCategory", "ContractCommitmentCost",
    "ContractCommitmentDescription", "ContractCommitmentId", "ContractCommitmentPeriodEnd",
    "ContractCommitmentPeriodStart", "ContractCommitmentQuantity", "ContractCommitmentType",
    "ContractCommitmentUnit", "ContractId", "ContractPeriodEnd", "ContractPeriodStart",
)


def _duration_gap(report):
    [gap] = [g for g in report.gaps["Contract Commitment"] if g.column == DURATION]
    return gap


def test_gaps_report_the_duration_as_a_conditional_advisory(source_tables):
    cau, _ = source_tables[("aws", "1.3")]
    report = compute_gaps(tuple(cau[0]), "1.3", cc_columns=CC_1_3)
    gap = _duration_gap(report)
    assert not gap.blocking and not gap.allows_nulls
    assert gap.supplement_kinds == ("contract_commitment",)
    assert f"~ {DURATION} (conditional: " in report.render_text()


def test_gaps_block_on_the_duration_without_a_commitment_period(source_tables):
    cau, _ = source_tables[("aws", "1.3")]
    header = tuple(c for c in CC_1_3 if c != "ContractCommitmentPeriodEnd")
    gap = _duration_gap(compute_gaps(tuple(cau[0]), "1.3", cc_columns=header))
    assert gap.blocking and gap.current_lineage == "UNAVAILABLE"
