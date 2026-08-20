"""Single-source residue arithmetic for Split Cost Allocation groups.

A valid allocation group must satisfy two exact-sum invariants at once: the
``AllocatedRatio`` values sum to exactly 1, and every allocated amount column sums to
exactly its origin total (no cents created or lost). Both are achieved the same way —
quantise every share but the last, and let the **last** share absorb the residue —
implemented here once and used by both the coherent scenario builders
(:mod:`focus_data_toolkit.generators.scenarios`) and the CSV generators.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

RATIO_QUANTUM = Decimal("0.000001")


def residue_ratios(weights: Sequence[Decimal], *, quantum: Decimal = RATIO_QUANTUM) -> list[Decimal]:
    """Ratios proportional to ``weights`` that sum to exactly 1 (last absorbs the residue)."""
    if not weights:
        raise ValueError("an allocation group needs at least one consumer")
    total = sum(weights, Decimal(0))
    if total == 0:
        raise ValueError("allocation weights sum to zero; cannot form ratios")
    ratios: list[Decimal] = []
    acc = Decimal(0)
    for w in weights[:-1]:
        ratio = (w / total).quantize(quantum)
        acc += ratio
        ratios.append(ratio)
    ratios.append(Decimal(1) - acc)
    return ratios


def residue_shares(
    total: Decimal, fractions: Sequence[Decimal], quantum: Decimal
) -> list[Decimal]:
    """Split ``total`` along ``fractions`` so the shares sum to exactly ``total``.

    Every share but the last is ``total * fraction`` quantised to ``quantum``; the last
    share is the exact remainder. ``fractions`` are expected to sum to (about) 1 — e.g.
    the output of :func:`residue_ratios`, or raw ``weight/total`` fractions.
    """
    if not fractions:
        raise ValueError("an allocation group needs at least one consumer")
    shares: list[Decimal] = []
    acc = Decimal(0)
    for fraction in fractions[:-1]:
        share = (total * fraction).quantize(quantum)
        acc += share
        shares.append(share)
    shares.append(total - acc)
    return shares
