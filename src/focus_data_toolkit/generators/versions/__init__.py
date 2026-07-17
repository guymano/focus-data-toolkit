"""FOCUS version adapters (1.2, 1.3).

An adapter owns everything that differs between FOCUS versions: the column set, the optional
Contract Commitment dataset, the scenario ladder, and the small per-version field hooks
(the 1.3 provider-identity split, ``PricingCurrencyEffectiveCost`` on Tax/Credit, and the
``ContractApplied`` link). Providers are version-agnostic; adapters are provider-agnostic.
"""

from __future__ import annotations

from focus_data_toolkit.generators.versions.adapter import LadderBranch, VersionAdapter
from focus_data_toolkit.generators.versions.v1_2 import V12
from focus_data_toolkit.generators.versions.v1_3 import V13

ADAPTERS: dict[str, VersionAdapter] = {V12.version: V12, V13.version: V13}

__all__ = ["ADAPTERS", "LadderBranch", "V12", "V13", "VersionAdapter"]
