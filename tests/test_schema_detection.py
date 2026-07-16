"""Schema/version detection (P1.1).

Real 1.2/1.3 Cost and Usage / Contract Commitment headers come from the generators, whose
column lists are hardcoded *independently* of the model-derived detection registry — so
these assertions cross-check two independent encodings. FOCUS 1.4 (no generator exists) and
the hand-crafted edge cases are built explicitly.
"""

from __future__ import annotations

import pytest

from focus_data_toolkit.convert.detect import detect_focus_version
from focus_data_toolkit.schema import registry
from focus_data_toolkit.schema.detection import detect_focus_schema

CAU_1_2 = sorted(registry.version_columns("Cost and Usage", "1.2"))
CAU_1_3 = sorted(registry.version_columns("Cost and Usage", "1.3"))
CAU_1_4 = sorted(registry.version_columns("Cost and Usage", "1.4"))
BP_1_4 = sorted(registry.version_columns("Billing Period", "1.4"))
IND_1_4 = sorted(registry.version_columns("Invoice Detail", "1.4"))
CC_1_3 = sorted(registry.version_columns("Contract Commitment", "1.3"))
CC_1_4 = sorted(registry.version_columns("Contract Commitment", "1.4"))


def test_generator_headers_detected_exactly(source_tables):
    for (provider, version), (cau, cc) in source_tables.items():
        result = detect_focus_schema(cau[0].keys())
        assert result.dataset == "Cost and Usage"
        assert result.detected_version == version
        assert result.confidence == "HIGH"
        assert result.exact_match, (provider, version, result.missing_columns)
        if cc is not None:
            cc_result = detect_focus_schema(cc[0].keys())
            assert cc_result.dataset == "Contract Commitment"
            assert cc_result.detected_version == "1.3"


@pytest.mark.parametrize(
    "cols, version",
    [(CAU_1_2, "1.2"), (CAU_1_3, "1.3"), (CAU_1_4, "1.4")],
)
def test_cost_and_usage_versions_exact(cols, version):
    result = detect_focus_schema(cols)
    assert result.dataset == "Cost and Usage"
    assert result.detected_version == version
    assert result.confidence == "HIGH"
    assert result.exact_match


@pytest.mark.parametrize("dataset, cols", [
    ("Billing Period", BP_1_4),
    ("Invoice Detail", IND_1_4),
    ("Contract Commitment", CC_1_4),
])
def test_other_datasets_detected(dataset, cols):
    result = detect_focus_schema(cols)
    assert result.dataset == dataset
    assert result.detected_version == "1.4"
    assert result.confidence == "HIGH"


def test_contract_commitment_1_3_vs_1_4():
    assert detect_focus_schema(CC_1_3).detected_version == "1.3"
    assert detect_focus_schema(CC_1_4).detected_version == "1.4"


@pytest.mark.parametrize("cols, version", [(CAU_1_3, "1.3"), (CAU_1_4, "1.4")])
def test_extension_columns_do_not_break_detection(cols, version):
    result = detect_focus_schema([*cols, "x_MyCustom", "x_AnotherOne"])
    assert result.detected_version == version
    assert result.confidence == "HIGH"
    assert result.extension_columns == ("x_AnotherOne", "x_MyCustom")


def test_hybrid_1_3_1_4_is_ambiguous():
    # A 1.3 header carrying a 1.4-only column: not a clean match to either version.
    result = detect_focus_schema([*CAU_1_3, "InvoiceDetailId"])
    assert result.confidence != "HIGH"
    assert not result.exact_match
    assert "InvoiceDetailId" in result.additional_focus_columns
    assert ("Cost and Usage", "1.4") in result.ambiguous_candidates


def test_incomplete_file_flags_missing_mandatory():
    mandatory = sorted(registry.mandatory_columns("Cost and Usage", "1.3"))
    dropped = mandatory[:3]
    result = detect_focus_schema([c for c in CAU_1_3 if c not in dropped])
    assert result.dataset == "Cost and Usage"
    assert result.confidence != "HIGH"
    assert set(dropped) <= set(result.missing_columns)


def test_non_focus_header_has_no_dataset():
    result = detect_focus_schema(["Date", "Amount", "Account", "Description"])
    assert result.dataset is None
    assert result.detected_version is None
    assert result.confidence == "LOW"


def test_unknown_columns_are_reported():
    result = detect_focus_schema([*CAU_1_3, "MyRandomColumn"])
    assert result.unknown_columns == ("MyRandomColumn",)
    assert result.confidence != "HIGH"


def test_forced_version_correct():
    result = detect_focus_schema(CAU_1_3, version="1.3")
    assert result.forced
    assert result.detected_version == "1.3"
    assert result.confidence == "HIGH"


def test_forced_version_incompatible():
    result = detect_focus_schema(CAU_1_4, version="1.2")
    assert result.forced
    assert result.confidence == "LOW"
    # 1.4-only columns present are what makes 1.2 impossible.
    assert "InvoiceDetailId" in result.additional_focus_columns


def test_forced_version_on_non_focus_header_is_low():
    # Forcing a version does not override a header with essentially no FOCUS overlap.
    result = detect_focus_schema(["Date", "Amount", "Account"], version="1.3")
    assert result.confidence == "LOW"


def test_forced_dataset():
    result = detect_focus_schema(CAU_1_3, dataset="cost-and-usage")
    assert result.dataset == "Cost and Usage"
    assert result.detected_version == "1.3"


def test_forced_dataset_version_that_does_not_exist():
    result = detect_focus_schema(IND_1_4, dataset="invoice-detail", version="1.2")
    assert result.confidence == "LOW"


def test_removed_columns_still_present():
    # A 1.4 header that still carries ProviderName (removed in 1.4).
    result = detect_focus_schema([*CAU_1_4, "ProviderName"])
    assert result.detected_version == "1.4"
    assert "ProviderName" in result.additional_focus_columns
    assert result.confidence != "HIGH"


def test_column_order_does_not_matter():
    reordered = list(reversed(CAU_1_3))
    result = detect_focus_schema(reordered)
    assert result.detected_version == "1.3"
    assert result.confidence == "HIGH"
    assert result.exact_match


def test_detect_focus_version_compat(source_tables):
    for (_provider, version), (cau, _cc) in source_tables.items():
        assert detect_focus_version(cau[0].keys()) == version


def test_detect_focus_version_rejects_non_focus():
    with pytest.raises(ValueError, match="not a FOCUS 1.2 or 1.3"):
        detect_focus_version(["Date", "Amount", "Account"])


def test_detect_focus_version_rejects_1_4():
    # A 1.4 Cost and Usage source is not convertible by this tool (1.x -> 1.4 only).
    with pytest.raises(ValueError, match="not a FOCUS 1.2 or 1.3"):
        detect_focus_version(CAU_1_4)


def test_foreign_dataset_column_is_flagged():
    # PaymentTerms is an Invoice Detail column; on a Cost and Usage header it must not be
    # silently ignored (it would otherwise be dropped in strict conversion).
    result = detect_focus_schema([*CAU_1_3, "PaymentTerms"])
    assert result.confidence != "HIGH"
    assert not result.exact_match
    assert "PaymentTerms" in result.additional_focus_columns


def test_malformed_none_header_does_not_crash():
    # csv.DictReader emits a None key for surplus fields; detection must not raise.
    result = detect_focus_schema([*CAU_1_3, None])
    assert result.dataset == "Cost and Usage"
    assert not result.exact_match
    assert any("malformed" in note for note in result.notes)


def test_1_2_missing_provider_columns_not_high_confidence():
    # A 1.2 header without ProviderName/PublisherName cannot derive the 1.4-Mandatory
    # ServiceProviderName/HostProviderName, so it must not be HIGH confidence.
    result = detect_focus_schema([c for c in CAU_1_2 if c not in ("ProviderName", "PublisherName")])
    assert result.confidence != "HIGH"
    assert {"ProviderName", "PublisherName"} <= set(result.missing_columns)


def test_detection_result_is_json_serialisable():
    import json

    result = detect_focus_schema(CAU_1_3)
    payload = json.dumps(result.as_dict())
    assert '"detected_version": "1.3"' in payload
