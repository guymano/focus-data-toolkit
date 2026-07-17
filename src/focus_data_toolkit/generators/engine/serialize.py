"""CSV serialization and the ``python -m`` CLI shared by every generator module.

Serialization is kept separate from generation: the row builders never touch CSV, and these
functions never invent data.
"""

from __future__ import annotations

import argparse
import csv
import io
from datetime import timedelta
from pathlib import Path

from focus_data_toolkit.generators.engine.determinism import (
    COMMIT_TERM_DAYS,
    contract_id_for,
    iso,
    parse_iso,
)
from focus_data_toolkit.generators.engine.ladder import generate_rows

DEFAULT_ROWS = 1000


def generate_csv_bytes(
    rows: int = DEFAULT_ROWS,
    seed: int | None = None,
    *,
    include_credits: bool = False,
    profile,
    adapter,
) -> bytes:
    """Serialise the Cost and Usage rows to deterministic UTF-8 CSV bytes (LF line endings)."""
    if seed is None:
        seed = adapter.default_seed
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(adapter.columns), lineterminator="\n")
    writer.writeheader()
    for record in generate_rows(rows, seed, include_credits=include_credits, profile=profile, adapter=adapter):
        writer.writerow(record)
    return buffer.getvalue().encode("utf-8")


def generate_contract_commitment_rows(
    rows: int = DEFAULT_ROWS,
    seed: int | None = None,
    *,
    profile,
    adapter,
) -> list[dict[str, str]]:
    """Return the Contract Commitment dataset for the same (rows, seed).

    Each commitment Purchase row yields exactly one Contract Commitment row, so
    ``ContractCommitmentId`` == ``CommitmentDiscountId`` is a verifiable foreign key.
    """
    if adapter.contract_commitment_columns is None:
        raise ValueError(f"FOCUS {adapter.version} has no Contract Commitment dataset")
    if seed is None:
        seed = adapter.default_seed
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for cu in generate_rows(rows, seed, include_credits=False, profile=profile, adapter=adapter):
        if cu["ChargeCategory"] != "Purchase" or not cu["CommitmentDiscountId"]:
            continue
        commit_id = cu["CommitmentDiscountId"]
        if commit_id in seen:
            continue
        seen.add(commit_id)
        period_start = parse_iso(cu["ChargePeriodStart"])
        period_end = period_start + timedelta(days=COMMIT_TERM_DAYS)
        contract_id = contract_id_for(commit_id)
        row = {name: "" for name in adapter.contract_commitment_columns}
        row["ContractCommitmentId"] = commit_id
        row["ContractCommitmentType"] = cu["CommitmentDiscountType"]
        row["ContractCommitmentCategory"] = cu["CommitmentDiscountCategory"]
        row["ContractCommitmentCost"] = cu["BilledCost"]  # the upfront commitment cost
        row["ContractCommitmentQuantity"] = cu["CommitmentDiscountQuantity"]
        row["ContractCommitmentUnit"] = cu["CommitmentDiscountUnit"]
        row["ContractCommitmentDescription"] = cu["CommitmentDiscountName"]
        row["ContractCommitmentPeriodStart"] = iso(period_start)
        row["ContractCommitmentPeriodEnd"] = iso(period_end)
        row["ContractId"] = contract_id
        row["ContractPeriodStart"] = iso(period_start)
        row["ContractPeriodEnd"] = iso(period_end)
        row["BillingCurrency"] = "USD"
        out.append(row)
    return out


def generate_contract_commitment_csv_bytes(
    rows: int = DEFAULT_ROWS,
    seed: int | None = None,
    *,
    profile,
    adapter,
) -> bytes:
    """Serialise the Contract Commitment dataset to deterministic UTF-8 CSV bytes (LF)."""
    if seed is None:
        seed = adapter.default_seed
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(adapter.contract_commitment_columns), lineterminator="\n"
    )
    writer.writeheader()
    for record in generate_contract_commitment_rows(rows, seed, profile=profile, adapter=adapter):
        writer.writerow(record)
    return buffer.getvalue().encode("utf-8")


def main(argv: list[str] | None = None, *, profile, adapter) -> int:
    """``python -m focus_data_toolkit.generators.generate_<provider>_focus_<version>`` entry point."""
    label = f"{profile.provider_name} FOCUS {adapter.version}"
    has_cc = adapter.contract_commitment_columns is not None
    parser = argparse.ArgumentParser(description=f"Generate synthetic {label} CSV data.")
    if has_cc:
        parser.add_argument(
            "--dataset",
            choices=("cost_and_usage", "contract_commitment"),
            default="cost_and_usage",
            help="FOCUS dataset to emit (default: cost_and_usage)",
        )
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS, help="number of data rows")
    parser.add_argument("--seed", type=int, default=adapter.default_seed, help="deterministic RNG seed")
    parser.add_argument("--out", type=Path, default=None, help="output CSV path")
    parser.add_argument(
        "--include-credits",
        action="store_true",
        help="emit some Credit rows with negative BilledCost (excluded from the default fixture)",
    )
    args = parser.parse_args(argv)

    dataset = getattr(args, "dataset", "cost_and_usage")
    default_stem = f"focus_sample_{{}}_{profile.key}"
    if dataset == "contract_commitment":
        payload = generate_contract_commitment_csv_bytes(
            args.rows, args.seed, profile=profile, adapter=adapter
        )
        out = args.out or Path(f"{default_stem.format('contractcommitment')}.csv")
        columns = adapter.contract_commitment_columns
    else:
        payload = generate_csv_bytes(
            args.rows, args.seed, include_credits=args.include_credits, profile=profile, adapter=adapter
        )
        out = args.out or Path(f"{default_stem.format('costandusage')}_{args.rows}.csv")
        columns = adapter.columns

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    print(f"Wrote {dataset} ({len(columns)} {label} columns) -> {out}")
    return 0
