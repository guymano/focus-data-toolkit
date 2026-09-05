"""Small value objects threaded through the engine row builders.

``ResourceRef`` / ``RowContext`` live in :mod:`focus_data_toolkit.generators.providers.profile`
next to the :class:`ServiceSpec` they reference, so the import graph stays a one-way street
(engine -> providers) with no cycle. Re-exported here for the engine-side importers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from focus_data_toolkit.generators.engine.determinism import stable_id
from focus_data_toolkit.generators.providers.profile import ResourceRef, RowContext


@dataclass
class GenerationContext:
    """Per-call contract registry; never shared between providers or invocations."""

    contracts: dict[str, str] = field(default_factory=dict)
    purchases: dict[str, dict[str, str]] = field(default_factory=dict)
    counts: dict[tuple[str, ...], int] = field(default_factory=dict)

    def contract_for(self, row: dict[str, str], commitment_id: str) -> str:
        if commitment_id in self.contracts:
            raise ValueError(f"duplicate commitment id in generation: {commitment_id}")
        key = tuple(row[k] for k in (
            "ProviderName", "BillingAccountId", "SubAccountId", "BillingCurrency",
        ))
        index = self.counts.get(key, 0)
        self.counts[key] = index + 1
        contract = stable_id("CONTRACT-CD-", [*key, index // 3])
        self.contracts[commitment_id] = contract
        return contract

__all__ = ["GenerationContext", "ResourceRef", "RowContext"]
