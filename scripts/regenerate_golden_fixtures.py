#!/usr/bin/env python3
"""Regenerate the golden generator fixtures in tests/fixtures/golden/compatibility_golden/.

Replays the exact ``(provider, version, rows, seed, include_credits)`` grid asserted by
``tests/test_generator_golden.py`` — the Cost and Usage matrix, the 1.3 Contract
Commitment files and the coherent scenario snapshots — and rewrites every file.

Regenerating goldens is a **deliberate reproducibility break**: it requires a
CHANGELOG entry describing the output change and justification that the new bytes are
correct (see tests/fixtures/golden/README.md and docs/versioning.md). Run the test
suite afterwards — ``tests/test_generated_conformance.py`` proves the regenerated
bytes conformant, not merely reproducible.

Usage:  python scripts/regenerate_golden_fixtures.py
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from focus_data_toolkit.generators import PROVIDERS, get_generator, scenarios  # noqa: E402

GOLDEN = REPO / "tests" / "fixtures" / "golden" / "compatibility_golden"

# The same grid as tests/test_generator_golden.py::_CU_GRID.
CU_GRID = (
    {"rows": 25, "seed": 7},
    {"rows": 25, "seed": 7, "include_credits": True},
    {"rows": 100, "seed": 42},
)

SCENARIO_CASES = {
    "scenarios_sca_equal": lambda: scenarios.split_allocation_group("origin-1", "100.00", weights=[1, 1, 1]),
    "scenarios_sca_weighted": lambda: scenarios.split_allocation_group("origin-2", "100.00", weights=[3, 2, 1]),
    "scenarios_sca_negative": lambda: scenarios.split_allocation_group("origin-3", "-50.00", weights=[2, 3]),
    "scenarios_correction_set": lambda: scenarios.correction_set("chg-1", "100.00", ["-30.00", "5.00"]),
}


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


def main() -> int:
    written = 0
    for provider in PROVIDERS:
        for version in ("1.2", "1.3"):
            module = get_generator(provider, version)
            tag = version.replace(".", "_")
            for spec in CU_GRID:
                name_tag = f"rows{spec['rows']}_seed{spec['seed']}"
                if spec.get("include_credits"):
                    name_tag += "_credits"
                path = GOLDEN / f"{provider}_{tag}_cost_and_usage_{name_tag}.csv"
                path.write_bytes(module.generate_csv_bytes(**spec))
                print(f"wrote {path.relative_to(REPO)}")
                written += 1
            if version == "1.3":
                path = GOLDEN / f"{provider}_{tag}_contract_commitment_rows100_seed42.csv"
                path.write_bytes(module.generate_contract_commitment_csv_bytes(rows=100, seed=42))
                print(f"wrote {path.relative_to(REPO)}")
                written += 1
    for name, build in sorted(SCENARIO_CASES.items()):
        path = GOLDEN / f"{name}.csv"
        path.write_bytes(_rows_to_csv(build()))
        print(f"wrote {path.relative_to(REPO)}")
        written += 1
    print(f"{written} golden fixtures regenerated — run pytest and update CHANGELOG.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
