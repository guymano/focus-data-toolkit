"""The scenario-selection loop shared by every generator.

Draws one scenario-selection value per dispatch, then lets the selected scenario consume
its own draws (including tax source selection). Whole groups advance several row positions.
The version adapter's first matching threshold preserves the ``if/elif`` semantics.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from focus_data_toolkit.generators.engine import scenarios_core
from focus_data_toolkit.generators.engine.context import GenerationContext

DEFAULT_ROWS = 1000

# Generic builders return a row or a whole group; tax and commitment dispatch need
# generation-local context and are handled explicitly below.
_Builder = Callable[..., "dict[str, str] | list[dict[str, str]]"]

_BUILDERS: dict[str, _Builder] = {
    "credit": scenarios_core.credit_row,
    "purchase": scenarios_core.standalone_purchase_row,
    "split": scenarios_core.split_allocation_group_rows,
}


def generate_rows(
    rows: int = DEFAULT_ROWS,
    seed: int | None = None,
    *,
    include_credits: bool = False,
    profile,
    adapter,
    context: GenerationContext | None = None,
) -> list[dict[str, str]]:
    """Return ``rows`` synthetic records for ``profile``/``adapter`` as ordered string dicts.

    ``rows``/``seed`` default to the historical per-module values (1000 rows; the adapter's
    default seed) so the shim ``generate_rows()`` / ``generate_rows(rows=N)`` calls keep working.
    """
    if seed is None:
        seed = adapter.default_seed
    if rows < 1:
        raise ValueError("rows must be >= 1")
    rng = random.Random(seed)
    context = context if context is not None else GenerationContext()
    out: list[dict[str, str]] = []
    untaxed: list[int] = []
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
        built: dict[str, str] | list[dict[str, str]]
        if chosen is None:
            built = scenarios_core.usage_row(rng, i, remaining, profile, adapter)
        elif chosen.kind == "tax":
            if untaxed:
                index = untaxed.pop(rng.randrange(len(untaxed)))
                built = scenarios_core.tax_row(out[index], index + 1, adapter)
            else:
                built = scenarios_core.usage_row(rng, i, remaining, profile, adapter)
        elif chosen.kind == "commitment":
            built = scenarios_core.commitment_group(rng, i, remaining, profile, adapter, context)
        else:
            built = _BUILDERS[chosen.kind](rng, i, remaining, profile, adapter)
        if isinstance(built, list):
            if len(built) > remaining:
                raise ValueError("scenario exceeded its row budget")
            out.extend(built or [scenarios_core.usage_row(rng, i, remaining, profile, adapter)])
        else:
            out.append(built)
        untaxed.extend(j for j in range(i, len(out)) if
            out[j]["ChargeCategory"] == "Usage" and out[j]["PricingCategory"] == "Standard")
    if len(out) != rows:
        raise ValueError("generator did not respect the requested row count")
    return out
