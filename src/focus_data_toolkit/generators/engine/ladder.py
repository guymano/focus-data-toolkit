"""The scenario-selection loop shared by every generator.

Draws exactly one ``rng.random()`` per output position (as the historical generators did) and
dispatches to a scenario builder via the version adapter's ladder, preserving the original
``if/elif`` semantics precisely.
"""

from __future__ import annotations

import random

from focus_data_toolkit.generators.engine import scenarios_core

_BUILDERS = {
    "credit": scenarios_core.credit_row,
    "tax": scenarios_core.tax_row,
    "purchase": scenarios_core.standalone_purchase_row,
    "split": scenarios_core.split_allocation_row,
    "commitment": scenarios_core.commitment_group,
}


def generate_rows(
    rows: int,
    seed: int,
    *,
    include_credits: bool = False,
    profile,
    adapter,
) -> list[dict[str, str]]:
    """Return ``rows`` synthetic records for ``profile``/``adapter`` as ordered string dicts."""
    if rows < 1:
        raise ValueError("rows must be >= 1")
    rng = random.Random(seed)
    out: list[dict[str, str]] = []
    while len(out) < rows:
        i = len(out)
        remaining = rows - i
        roll = rng.random()
        chosen = None
        for branch in adapter.ladder:
            if branch.requires_credits and not include_credits:
                continue
            if roll < branch.threshold:
                if branch.min_remaining is None or remaining >= branch.min_remaining:
                    chosen = branch
                break  # first threshold match wins (elif semantics); guard failure -> Usage
        if chosen is None:
            out.append(scenarios_core.usage_row(rng, i, remaining, profile, adapter))
        elif chosen.group:
            out.extend(_BUILDERS[chosen.kind](rng, i, remaining, profile, adapter))
        else:
            out.append(_BUILDERS[chosen.kind](rng, i, remaining, profile, adapter))
    return out[:rows]
