"""Detect the FOCUS version of a Cost and Usage source from its column set.

This is a thin compatibility wrapper over :mod:`focus_data_toolkit.schema.detection`,
which does the real work (dataset + version + confidence). ``detect_focus_version`` keeps
the historical contract: it returns ``"1.2"`` or ``"1.3"`` for a convertible Cost and Usage
header and raises ``ValueError`` otherwise.
"""

from __future__ import annotations

from collections.abc import Iterable

from focus_data_toolkit.schema.detection import detect_focus_schema

_CONVERTIBLE_VERSIONS = ("1.2", "1.3")


def detect_focus_version(fieldnames: Iterable[str]) -> str:
    """Return ``"1.2"`` or ``"1.3"`` for a convertible Cost and Usage header row.

    Raises ``ValueError`` when the header is not a confidently-identified FOCUS 1.2/1.3
    Cost and Usage table (e.g. FOCUS 1.0/1.1, 1.4, a different dataset, or non-FOCUS data).
    """
    result = detect_focus_schema(fieldnames)
    if (
        result.dataset == "Cost and Usage"
        and result.detected_version in _CONVERTIBLE_VERSIONS
        and result.confidence != "LOW"
    ):
        return result.detected_version

    detail = (
        f"detected {result.dataset or 'no FOCUS dataset'} "
        f"{result.detected_version or ''} (confidence {result.confidence})".strip()
    )
    raise ValueError(
        "unsupported source: not a FOCUS 1.2 or 1.3 Cost and Usage header "
        f"[{detail}]"
    )
