from __future__ import annotations

import csv
import io

import pytest

from focus_data_toolkit.generators import get_generator

ROWS = 100
SEEDS = {"1.2": 1202, "1.3": 1302}


def _rows(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


@pytest.fixture(scope="session")
def source_tables():
    """{(provider, version): (cau_rows, cc_rows_or_None)} for the full matrix."""
    tables = {}
    for provider in ("aws", "azure", "gcp"):
        for version in ("1.2", "1.3"):
            module = get_generator(provider, version)
            cau = _rows(module.generate_csv_bytes(ROWS, SEEDS[version]))
            cc = (
                _rows(module.generate_contract_commitment_csv_bytes(ROWS, SEEDS[version]))
                if version == "1.3"
                else None
            )
            tables[(provider, version)] = (cau, cc)
    return tables
