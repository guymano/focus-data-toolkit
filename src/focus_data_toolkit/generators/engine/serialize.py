"""CSV serialization and the ``python -m`` CLI shared by every generator module.

Serialization is kept separate from generation: the row builders never touch CSV, and these
functions never invent data.
"""

from __future__ import annotations

import argparse
import csv
import io
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from focus_data_toolkit.generators.engine.context import GenerationContext
from focus_data_toolkit.generators.engine.determinism import (
    BILLING_START,
    COMMIT_TERM_DAYS,
    COMMIT_TERM_HOURS,
    CONTRACT_LEAD_DAYS,
    QTY_Q,
    iso,
    negotiated_commitment_id,
    negotiated_contract_id,
    parse_iso,
    q,
    s,
)
from focus_data_toolkit.generators.engine.ladder import generate_rows

DEFAULT_ROWS = 1000


def generate_bundle_csv_bytes(rows: int, seed: int | None, *, profile, adapter) -> tuple[bytes, bytes | None]:
    """Internal CLI bundle: one generation pass and one contract registry."""
    context = GenerationContext()
    records = generate_rows(rows, seed, profile=profile, adapter=adapter, context=context)

    def encode(columns, data):
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        writer.writerows(data)
        return buffer.getvalue().encode("utf-8")

    cau = encode(adapter.columns, records)
    cc = (encode(adapter.contract_commitment_columns, contract_rows(context, profile, adapter))
          if adapter.contract_commitment_columns else None)
    return cau, cc

# Negotiated contract terms that are NOT commitment discounts: (kind, category, type,
# description, term cost, term quantity, unit). Spend terms carry a cost with no
# quantity/unit; the usage term carries a real measured quantity in its native unit.
# They share one multi-commitment ContractId per provider and are reachable from Cost
# and Usage exclusively through ``ContractApplied`` (see the 1.3 adapter).
_NEGOTIATED_TERMS: tuple[tuple[str, str, str, str, str, str, str], ...] = (
    (
        "MINSPEND", "Spend", "Minimum Spend",
        "Contracted minimum spend across eligible services", "120000", "", "",
    ),
    (
        "RATECARD", "Spend", "Negotiated Rate Card",
        "Negotiated rate card applied to eligible on-demand usage", "250000", "", "",
    ),
    (
        "USAGEMIN", "Usage", "Usage Commitment",
        "Contracted minimum usage across eligible services", "150000", "100000.0000", "Hours",
    ),
)


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
    include_credits: bool = False,
    profile,
    adapter,
) -> list[dict[str, str]]:
    """Serialize annual terms from the same per-call registry and generation options."""
    if adapter.contract_commitment_columns is None:
        raise ValueError(f"FOCUS {adapter.version} has no Contract Commitment dataset")
    context = GenerationContext()
    generate_rows(rows, seed, include_credits=include_credits, profile=profile, adapter=adapter,
                  context=context)
    return contract_rows(context, profile, adapter)


def contract_rows(context: GenerationContext, profile, adapter) -> list[dict[str, str]]:
    """Build both parent periods and term totals from the registered purchases."""
    out: list[dict[str, str]] = []
    for commit_id, cu in context.purchases.items():
        spend_based = cu["CommitmentDiscountCategory"] == "Spend"
        # The first Purchase row of the commitment: its charge period opens the
        # commitment period, its BilledCost is the per-period fee.
        term_cost = Decimal(cu["BilledCost"]) * COMMIT_TERM_HOURS
        period_start = parse_iso(cu["ChargePeriodStart"])
        period_end = period_start + timedelta(days=COMMIT_TERM_DAYS)
        row = {name: "" for name in adapter.contract_commitment_columns}
        row["ContractCommitmentId"] = commit_id
        row["ContractCommitmentType"] = cu["CommitmentDiscountType"]
        row["ContractCommitmentCategory"] = cu["CommitmentDiscountCategory"]
        row["ContractCommitmentCost"] = s(term_cost)
        if not spend_based:
            # Four decimals so a CSV loader types the (integral) quantity as Decimal.
            row["ContractCommitmentQuantity"] = s(
                q(Decimal(cu["CommitmentDiscountQuantity"]) * COMMIT_TERM_HOURS, QTY_Q)
            )
            row["ContractCommitmentUnit"] = cu["CommitmentDiscountUnit"]
        row["ContractCommitmentDescription"] = cu["CommitmentDiscountName"]
        row["ContractCommitmentPeriodStart"] = iso(period_start)
        row["ContractCommitmentPeriodEnd"] = iso(period_end)
        row["ContractId"] = context.contracts[commit_id]
        row["ContractPeriodStart"] = iso(period_start - timedelta(days=CONTRACT_LEAD_DAYS))
        row["ContractPeriodEnd"] = iso(period_end + timedelta(days=CONTRACT_LEAD_DAYS))
        row["BillingCurrency"] = "USD"
        out.append(row)
    out.extend(_negotiated_rows(profile, adapter))
    spans: dict[str, tuple[str, str]] = {}
    for row in out:
        start, end = row["ContractPeriodStart"], row["ContractPeriodEnd"]
        old_start, old_end = spans.get(row["ContractId"], (start, end))
        spans[row["ContractId"]] = min(start, old_start), max(end, old_end)
    for row in out:
        row["ContractPeriodStart"], row["ContractPeriodEnd"] = spans[row["ContractId"]]
    return out


def _negotiated_rows(profile, adapter) -> list[dict[str, str]]:
    """The three negotiated (non-discount) contract terms, RNG-free and deterministic."""
    contract_id = negotiated_contract_id(profile.key)
    period_start = BILLING_START
    period_end = period_start + timedelta(days=COMMIT_TERM_DAYS)
    out: list[dict[str, str]] = []
    for kind, category, type_, description, cost, quantity, unit in _NEGOTIATED_TERMS:
        row = {name: "" for name in adapter.contract_commitment_columns}
        row["ContractCommitmentId"] = negotiated_commitment_id(kind, profile.key)
        row["ContractCommitmentType"] = type_
        row["ContractCommitmentCategory"] = category
        row["ContractCommitmentCost"] = cost
        row["ContractCommitmentQuantity"] = quantity
        row["ContractCommitmentUnit"] = unit
        row["ContractCommitmentDescription"] = description
        row["ContractCommitmentPeriodStart"] = iso(period_start)
        row["ContractCommitmentPeriodEnd"] = iso(period_end)
        row["ContractId"] = contract_id
        row["ContractPeriodStart"] = iso(period_start - timedelta(days=CONTRACT_LEAD_DAYS))
        row["ContractPeriodEnd"] = iso(period_end + timedelta(days=CONTRACT_LEAD_DAYS))
        row["BillingCurrency"] = "USD"
        out.append(row)
    return out


def generate_contract_commitment_csv_bytes(
    rows: int = DEFAULT_ROWS,
    seed: int | None = None,
    *,
    include_credits: bool = False,
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
    for record in generate_contract_commitment_rows(rows, seed, include_credits=include_credits, profile=profile, adapter=adapter):
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
            args.rows, args.seed, include_credits=args.include_credits, profile=profile, adapter=adapter
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
