from __future__ import annotations

import pytest

from focus_data_toolkit.convert.detect import detect_focus_version


def test_detects_both_versions(source_tables):
    for (provider, version), (cau, _) in source_tables.items():
        assert detect_focus_version(cau[0].keys()) == version, (provider, version)


def test_rejects_non_focus_header():
    with pytest.raises(ValueError, match="not a FOCUS 1.2 or 1.3"):
        detect_focus_version(["Date", "Amount", "Account"])


def test_rejects_focus_1_0_shape():
    # FOCUS 1.0 uses a single Region column (no RegionId): unsupported.
    with pytest.raises(ValueError):
        detect_focus_version(["ProviderName", "BilledCost", "ChargeCategory", "Region"])
