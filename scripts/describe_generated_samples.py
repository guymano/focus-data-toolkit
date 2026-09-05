"""Recalculate and check documented statistics; --write explicitly refreshes them."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from focus_data_toolkit.generators import PROVIDERS, get_generator  # noqa: E402
from scripts.sample_audit import audit, statistics  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    report = {}
    for provider in PROVIDERS:
        for version in ("1.2", "1.3"):
            m = get_generator(provider, version)
            rows = m.generate_rows(1000)
            cc = m.generate_contract_commitment_rows(1000) if version == "1.3" else None
            audit(rows, provider, version, cc)
            data = statistics(rows, provider)
            if cc is not None:
                data.update(commitments=len(cc), contracts=len({r["ContractId"] for r in cc}), non_discount_terms=3)
            report[f"{provider}_{version}"] = data
    path = ROOT / "docs/generator-statistics.json"
    if args.write:
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif json.loads(path.read_text(encoding="utf-8")) != report:
        raise SystemExit("documented statistics differ from generation; review before --write")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
