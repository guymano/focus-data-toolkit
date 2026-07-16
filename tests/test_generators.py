from __future__ import annotations

import pytest

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
