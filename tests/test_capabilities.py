"""CapabilityProfile: explicit, recorded declaration of applicability conditions."""

from __future__ import annotations

import pytest

from focus_data_toolkit.convert import convert_to_focus_1_4
from focus_data_toolkit.model.capabilities import (
    KNOWN_CONDITIONS,
    CapabilityProfile,
)
from focus_data_toolkit.model.validator import (
    COND_UNIT_PRICING,
    lint_focus_1_4_structure,
)


def usage_row_without_sku() -> dict[str, str]:
    return {
        "ChargeCategory": "Usage",
        "BilledCost": "10.00",
        "BillingCurrency": "USD",
        "SkuId": "",
        "SkuPriceId": "",
    }


def test_unknown_condition_is_rejected():
    with pytest.raises(ValueError, match="unknown applicability condition"):
        CapabilityProfile(frozenset({"SupportsTimeTravel"}))


def test_profile_as_dict_is_deterministic():
    profile = CapabilityProfile.of(*sorted(KNOWN_CONDITIONS), source="api")
    assert profile.as_dict() == {
        "supported_conditions": sorted(KNOWN_CONDITIONS),
        "source": "api",
    }
    assert CapabilityProfile.none().as_dict() == {
        "supported_conditions": [],
        "source": "none-declared",
    }


def test_lint_enforces_declared_conditions_only():
    row = usage_row_without_sku()
    silent = lint_focus_1_4_structure("Cost and Usage", [row])
    assert "required_for_usage_or_purchase" not in {v.rule for v in silent.violations}

    enforced = lint_focus_1_4_structure(
        "Cost and Usage", [row], profile=CapabilityProfile.of(COND_UNIT_PRICING)
    )
    assert "required_for_usage_or_purchase" in {v.rule for v in enforced.violations}


def test_profile_unions_with_legacy_supported_conditions():
    row = usage_row_without_sku()
    report = lint_focus_1_4_structure(
        "Cost and Usage",
        [row],
        supported_conditions=[COND_UNIT_PRICING],
        profile=CapabilityProfile.none(),
    )
    assert "required_for_usage_or_purchase" in {v.rule for v in report.violations}


def test_manifest_records_the_active_profile(source_tables):
    cau, cc = source_tables[("aws", "1.3")]
    default = convert_to_focus_1_4(cau, cc, validate=False)
    assert default.manifest["capability_profile"] == {
        "supported_conditions": [],
        "source": "none-declared",
    }
    declared = convert_to_focus_1_4(
        cau, cc, validate=False, capabilities=CapabilityProfile.of(COND_UNIT_PRICING)
    )
    assert declared.manifest["capability_profile"]["supported_conditions"] == [
        COND_UNIT_PRICING
    ]
