"""Supplemental client data: complete a FOCUS 1.2/1.3 source into factual 1.4 datasets.

A Cost and Usage source alone cannot factually populate the provider-issued facts the
FOCUS 1.4 Billing Period / Invoice Detail / Contract Commitment datasets require. This
package lets a client supply those facts:

* :mod:`focus_data_toolkit.supplement.gaps` — compute exactly which columns are missing
  for a given source (the ``gaps`` CLI command) and which supplement satisfies each;
* :mod:`focus_data_toolkit.supplement.kinds` — the registry of supplement kinds (their
  target dataset, join keys and allowed fact columns).

Supplied values carry ``ENRICHED`` lineage with full attribution, so a fully covered
dataset becomes strictly producible without any code change to the strict gate.
"""

from __future__ import annotations

from focus_data_toolkit.supplement.gaps import ColumnGap, GapReport, compute_gaps
from focus_data_toolkit.supplement.kinds import SUPPLEMENT_KINDS, SupplementKind

__all__ = [
    "SUPPLEMENT_KINDS",
    "ColumnGap",
    "GapReport",
    "SupplementKind",
    "compute_gaps",
]
