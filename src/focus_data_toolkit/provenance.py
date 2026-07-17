"""Value provenance (lineage) for converted FOCUS 1.4 columns.

Every produced column is classified by *how* its value was obtained. The headline
classification is a property of the conversion **rule** for a column, so it is
deterministic and drives both the manifest and the strict-mode gating: in ``STRICT``
mode a dataset is produced only when every Mandatory non-nullable column has a
**factual** lineage (not assumed / unavailable).

Some rules act differently per row (e.g. a backfill only touches null source
values). For those columns the headline rule stays the *weakest* lineage the rule
can produce (conservative for gating), and a :class:`LineageCounters` accumulator
records how many values actually took each lineage — surfaced in the manifest as
``lineage_summary`` so a column-level label never hides the per-value mix.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum


class Lineage(StrEnum):
    OBSERVED = "OBSERVED"        # value present directly in the source
    RENAMED = "RENAMED"          # copied from an equivalent column, name change only
    DERIVED = "DERIVED"          # computed exactly and verifiably from source values
    ENRICHED = "ENRICHED"        # from a complementary authoritative source/context
    ASSUMED = "ASSUMED"          # hypothetical value (not a source fact)
    UNAVAILABLE = "UNAVAILABLE"  # absent and not derivable (emitted null)


# Lineages that represent real, non-fabricated data. A Mandatory non-nullable column
# whose lineage is NOT factual blocks strict production of its dataset.
FACTUAL_LINEAGES: frozenset[Lineage] = frozenset(
    {Lineage.OBSERVED, Lineage.RENAMED, Lineage.DERIVED, Lineage.ENRICHED}
)


@dataclass(frozen=True)
class ColumnRule:
    """How one target column's value is obtained during conversion."""

    lineage: Lineage
    source: str | None = None
    note: str | None = None

    @property
    def is_factual(self) -> bool:
        return self.lineage in FACTUAL_LINEAGES

    @property
    def is_assumed(self) -> bool:
        return self.lineage is Lineage.ASSUMED

    def as_dict(self) -> dict[str, str]:
        out: dict[str, str] = {"lineage": self.lineage.value}
        if self.source:
            out["source"] = self.source
        if self.note:
            out["note"] = self.note
        return out


def strict_blockers(provenance: dict[str, ColumnRule], columns: dict[str, dict]) -> list[str]:
    """Return the Mandatory non-nullable columns that block strict production.

    ``columns`` is the model's column spec map for the dataset. A column blocks when it
    is Mandatory and non-nullable and its lineage is not factual (assumed, unavailable,
    or missing from ``provenance``).
    """
    blockers: list[str] = []
    for col, spec in columns.items():
        if spec.get("feature_level") != "Mandatory" or spec.get("allows_nulls", True):
            continue
        rule = provenance.get(col)
        if rule is None or not rule.is_factual:
            blockers.append(col)
    return sorted(blockers)


def has_assumptions(provenance: dict[str, ColumnRule]) -> bool:
    return any(rule.is_assumed for rule in provenance.values())


class LineageCounters:
    """Per-column counts of the lineage each emitted value actually took.

    Bounded (columns x lineage categories) and deterministic, so the eager and
    streaming pipelines produce identical summaries for the same input.
    """

    def __init__(self) -> None:
        self._counts: defaultdict[str, Counter[str]] = defaultdict(Counter)

    def record(self, column: str, lineage: Lineage, n: int = 1) -> None:
        self._counts[column][lineage.value] += n

    def summary(self) -> dict[str, dict[str, int]]:
        """Sorted ``{column: {lineage: count}}`` (deterministic manifest payload)."""
        return {
            column: {lineage: count for lineage, count in sorted(counts.items())}
            for column, counts in sorted(self._counts.items())
        }

    def __bool__(self) -> bool:
        return bool(self._counts)
