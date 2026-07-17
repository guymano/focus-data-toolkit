"""Capability profile — which FOCUS applicability conditions a source supports.

Several FOCUS columns are only *conditionally* required (e.g. ``SkuId`` /
``SkuPriceId`` when the provider supports unit pricing). The linter can only
enforce those requirements when the caller declares which conditions apply; an
undeclared condition is **not evaluated**, never silently assumed either way.

A :class:`CapabilityProfile` makes that declaration explicit and auditable: the
conversion pipeline records the active profile in the manifest, so a clean lint
can always be read together with the exact set of conditions it enforced.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from focus_data_toolkit.model.validator import (
    COND_MULTIPLE_PRICING_CATEGORIES,
    COND_UNIT_PRICING,
)

# All applicability conditions the linter knows how to enforce.
KNOWN_CONDITIONS: frozenset[str] = frozenset(
    {COND_MULTIPLE_PRICING_CATEGORIES, COND_UNIT_PRICING}
)


@dataclass(frozen=True)
class CapabilityProfile:
    """An explicit, validated declaration of supported applicability conditions.

    ``source`` documents where the declaration came from (e.g. ``"cli"``,
    ``"api"``); the default profile declares nothing and is labelled
    ``"none-declared"`` so an unevaluated condition set is visible, not silent.
    """

    supported_conditions: frozenset[str] = field(default_factory=frozenset)
    source: str = "none-declared"

    def __post_init__(self) -> None:
        # Accept any iterable-of-str the caller passed and materialize it exactly once —
        # a one-shot generator must not be exhausted by validation before it is frozen.
        conditions = frozenset(self.supported_conditions)
        object.__setattr__(self, "supported_conditions", conditions)
        unknown = conditions - KNOWN_CONDITIONS
        if unknown:
            known = ", ".join(sorted(KNOWN_CONDITIONS))
            raise ValueError(
                f"unknown applicability condition(s) {sorted(unknown)}; known: {known}"
            )

    @classmethod
    def none(cls) -> CapabilityProfile:
        """The default profile: no condition declared (none enforced)."""
        return cls()

    @classmethod
    def of(cls, *conditions: str, source: str = "api") -> CapabilityProfile:
        return cls(frozenset(conditions), source)

    def as_dict(self) -> dict:
        """Deterministic manifest payload."""
        return {
            "supported_conditions": sorted(self.supported_conditions),
            "source": self.source,
        }
