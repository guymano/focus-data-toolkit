from __future__ import annotations

import json

import pytest

from focus_data_toolkit.convert.contract_applied import parse as parse_contract_applied
from focus_data_toolkit.generators import FOCUS_VERSIONS, PROVIDERS, get_generator


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("version", FOCUS_VERSIONS)
def test_generators_are_byte_reproducible(provider, version):
    module = get_generator(provider, version)
    assert module.generate_csv_bytes(25, 7) == module.generate_csv_bytes(25, 7)


def test_column_counts(source_tables):
    for (provider, version), (cau, cc) in source_tables.items():
        expected = 57 if version == "1.2" else 65
        assert len(cau[0]) == expected, (provider, version)
        if cc is not None:
            assert len(cc[0]) == 13, provider


def test_unknown_provider_or_version_rejected():
    with pytest.raises(ValueError):
        get_generator("oracle", "1.2")
    with pytest.raises(ValueError):
        get_generator("aws", "1.4")


# --- Spec conformance of generated JSON columns (D3 audit) ----------------- #
@pytest.mark.parametrize("provider", PROVIDERS)
def test_generated_contract_applied_is_conformant_1_3(source_tables, provider):
    cau, _ = source_tables[(provider, "1.3")]
    seen = 0
    for row in cau:
        value = row.get("ContractApplied", "")
        if not value:
            continue
        parse_contract_applied(value, version="1.3")  # raises if non-conformant
        assert '"ContractCommitmentAppliedCost":' in value  # spec key name
        assert '"AppliedCost"' not in value  # not the short/legacy name
        seen += 1
    assert seen, f"{provider}: expected some ContractApplied rows"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_generated_allocated_method_details_uses_json_numbers(source_tables, provider):
    cau, _ = source_tables[(provider, "1.3")]
    seen = 0
    for row in cau:
        value = row.get("AllocatedMethodDetails", "")
        if not value:
            continue
        element = json.loads(value)["Elements"][0]
        assert isinstance(element["AllocatedRatio"], (int, float))
        assert isinstance(element["UsageQuantity"], (int, float))
        seen += 1
    assert seen, f"{provider}: expected some Split Cost Allocation rows"


@pytest.mark.parametrize("version", FOCUS_VERSIONS)
@pytest.mark.parametrize("provider", PROVIDERS)
def test_generated_sku_price_details_uses_focus_keys(source_tables, provider, version):
    # StorageClass / Redundancy are FOCUS-defined keys -> never x_-prefixed.
    cau = source_tables[(provider, version)][0]
    for row in cau:
        value = row.get("SkuPriceDetails", "")
        if value:
            assert "x_StorageClass" not in value and "x_Redundancy" not in value
