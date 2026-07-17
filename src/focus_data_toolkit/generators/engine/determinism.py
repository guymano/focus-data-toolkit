"""Shared deterministic helpers and constants for the generators.

Every value here is identical across all providers and FOCUS versions (verified against
the six historical generator modules). Rounding rules (``ROUND_HALF_UP`` + the quanta) and
the billing window live in exactly one place so they can never drift between providers.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

# --------------------------------------------------------------------------- #
# Billing window (fixed timestamps -> no clock -> byte-reproducible)
# --------------------------------------------------------------------------- #
BILLING_START = datetime(2026, 5, 1, tzinfo=UTC)
BILLING_END = datetime(2026, 6, 1, tzinfo=UTC)
PERIOD_DAYS = 28
PERIOD_HOURS = PERIOD_DAYS * 24
COMMIT_TERM_DAYS = 365  # 1-year commitment / contract term

# --------------------------------------------------------------------------- #
# Rounding quanta and commercial rates (single source of truth)
# --------------------------------------------------------------------------- #
COST_Q = Decimal("0.000001")
PRICE_Q = Decimal("0.0000000001")
QTY_Q = Decimal("0.0001")
EUR_PER_USD = Decimal("0.92")
COMMIT_RATE = Decimal("0.667")  # amortised commitment rate vs on-demand list
PRIVATE_RATE = Decimal("0.90")  # negotiated (contracted) rate vs list for on-demand
COMMIT_TERM_HOURS = Decimal("8760")  # 1-year reservation term

# --------------------------------------------------------------------------- #
# Shared value pools and FOCUS vocabularies
# --------------------------------------------------------------------------- #
ENVIRONMENTS = ("prod", "staging", "dev")
COST_CENTERS = ("cc-1042", "cc-2087", "cc-3110")
OWNERS = ("team-platform", "team-data", "team-payments")

PRICING_CATEGORIES: tuple[str, ...] = ("Standard", "Dynamic", "Committed", "Other")
# FOCUS-defined SkuPriceDetails property keys (others MUST be x_-prefixed).
FOCUS_SKU_PRICE_KEYS: frozenset[str] = frozenset(
    {
        "CoreCount",
        "MemorySize",
        "InstanceType",
        "InstanceSeries",
        "OperatingSystem",
        "DiskType",
        "DiskSpace",
        "DiskMaxIops",
        "GpuCount",
        "NetworkMaxIops",
        "NetworkMaxThroughput",
    }
)

HEX_LOWER = "0123456789abcdef"
HEX_UPPER = "0123456789ABCDEF"


def q(value: Decimal, quant: Decimal) -> Decimal:
    """Quantise ``value`` to ``quant`` using banker-free ROUND_HALF_UP (FOCUS rounding)."""
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def s(value: Decimal) -> str:
    """Render a Decimal as fixed-point text (never scientific notation)."""
    return format(value, "f")


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def hexid(rng: random.Random, width: int, alphabet: str = HEX_LOWER) -> str:
    """Draw ``width`` characters from ``alphabet`` (default lowercase hex)."""
    return "".join(rng.choice(alphabet) for _ in range(width))


def period(i: int, granularity: str) -> tuple[str, str]:
    if granularity == "hourly":
        start = BILLING_START + timedelta(hours=i % PERIOD_HOURS)
        return iso(start), iso(start + timedelta(hours=1))
    if granularity == "daily":
        start = BILLING_START + timedelta(days=i % PERIOD_DAYS)
        return iso(start), iso(start + timedelta(days=1))
    return iso(BILLING_START), iso(BILLING_END)


def sku_price_details(spec_sku_details: dict[str, object]) -> str:
    return json.dumps(spec_sku_details, separators=(",", ":"))


def contract_id_for(commit_id: str) -> str:
    """Deterministic parent ContractId for a commitment id (shared by both 1.3 datasets)."""
    return f"CONTRACT-{commit_id.rsplit('/', 1)[-1][:12]}"


def set_currency(
    row: dict[str, str],
    pricing_currency: str,
    list_unit: Decimal,
    contracted_unit: Decimal,
    effective_cost: Decimal,
) -> None:
    row["PricingCurrency"] = pricing_currency
    fx = EUR_PER_USD if pricing_currency == "EUR" else Decimal("1")
    row["PricingCurrencyListUnitPrice"] = s(q(list_unit * fx, PRICE_Q))
    row["PricingCurrencyContractedUnitPrice"] = s(q(contracted_unit * fx, PRICE_Q))
    row["PricingCurrencyEffectiveCost"] = s(q(effective_cost * fx, COST_Q))
