"""Golden byte-for-byte snapshots of generator output (compatibility contract).

These fixtures were captured from the pre-refactor per-provider generators; the shared-engine
refactor (P2-A) must reproduce them exactly for every ``(provider, version, rows, seed)``.

A change to a fixture is a deliberate reproducibility break: it requires regenerating the
fixture *and* a CHANGELOG note, because synthetic output is a byte-for-byte contract for an
identical toolkit version (see docs/versioning.md). See ``tests/fixtures/golden/README.md``.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from focus_data_toolkit.generators import PROVIDERS, get_generator, scenarios

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "compatibility_golden"

_CU_GRID = (
    {"rows": 25, "seed": 7},
    {"rows": 25, "seed": 7, "include_credits": True},
    {"rows": 100, "seed": 42},
)


def _cu_cases():
    for provider in PROVIDERS:
        for version in ("1.2", "1.3"):
            for spec in _CU_GRID:
                tag = f"rows{spec['rows']}_seed{spec['seed']}"
                if spec.get("include_credits"):
                    tag += "_credits"
                name = f"{provider}_{version.replace('.', '_')}_cost_and_usage_{tag}.csv"
                yield pytest.param(provider, version, dict(spec), name, id=name[:-4])


@pytest.mark.parametrize(("provider", "version", "spec", "name"), list(_cu_cases()))
def test_cost_and_usage_golden(provider, version, spec, name):
    module = get_generator(provider, version)
    assert module.generate_csv_bytes(**spec) == (GOLDEN / name).read_bytes()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_contract_commitment_golden(provider):
    module = get_generator(provider, "1.3")
    name = f"{provider}_1_3_contract_commitment_rows100_seed42.csv"
    assert module.generate_contract_commitment_csv_bytes(rows=100, seed=42) == (GOLDEN / name).read_bytes()


def _rows_to_csv(rows: list[dict[str, str]]) -> bytes:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


# The coherent scenarios share the single-source SCA JSON builder; these lock that
# reconciliation (generators + scenarios now emit identical AllocatedMethodDetails).
_SCENARIO_CASES = {
    "scenarios_sca_equal": lambda: scenarios.split_allocation_group("origin-1", "100.00", weights=[1, 1, 1]),
    "scenarios_sca_weighted": lambda: scenarios.split_allocation_group("origin-2", "100.00", weights=[3, 2, 1]),
    "scenarios_sca_negative": lambda: scenarios.split_allocation_group("origin-3", "-50.00", weights=[2, 3]),
    "scenarios_correction_set": lambda: scenarios.correction_set("chg-1", "100.00", ["-30.00", "5.00"]),
}


@pytest.mark.parametrize("name", sorted(_SCENARIO_CASES))
def test_scenario_golden(name):
    assert _rows_to_csv(_SCENARIO_CASES[name]()) == (GOLDEN / f"{name}.csv").read_bytes()
