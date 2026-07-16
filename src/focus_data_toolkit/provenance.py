"""Value provenance (lineage) for converted FOCUS 1.4 columns.

Every produced column is classified by *how* its value was obtained. Lineage is a
property of the conversion **rule** for a column (uniform across rows), so it is
deterministic and drives both the manifest and the strict-mode gating: in ``STRICT``
mode a dataset is produced only when every Mandatory non-nullable column has a
**factual** lineage (not assumed / unavailable).
"""

from __future__ import annotations

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
