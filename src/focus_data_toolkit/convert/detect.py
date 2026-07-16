"""Detect the FOCUS version of a Cost and Usage source from its column set."""

from __future__ import annotations

from collections.abc import Iterable

# Columns introduced by FOCUS 1.3 in the Cost and Usage dataset.
_FOCUS_1_3_MARKERS = frozenset(
    {"ServiceProviderName", "HostProviderName", "ContractApplied", "AllocatedMethodId"}
)
# Columns every supported FOCUS 1.2+ Cost and Usage source carries.
_FOCUS_1_2_MARKERS = frozenset({"ProviderName", "BilledCost", "ChargeCategory", "RegionId"})


def detect_focus_version(fieldnames: Iterable[str]) -> str:
    """Return ``"1.2"`` or ``"1.3"`` for a Cost and Usage header row.

    Raises ``ValueError`` when the header matches neither supported version
    (e.g. FOCUS 1.0/1.1 exports without ``RegionId``, or non-FOCUS data).
    """
    columns = set(fieldnames)
    if _FOCUS_1_3_MARKERS <= columns:
        return "1.3"
    if _FOCUS_1_2_MARKERS <= columns:
        return "1.2"
    missing = sorted(_FOCUS_1_2_MARKERS - columns)
    raise ValueError(
        "unsupported source: not a FOCUS 1.2 or 1.3 Cost and Usage header "
        f"(missing at least {missing})"
    )
