"""The ``VersionAdapter`` strategy object and the scenario-ladder description."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from focus_data_toolkit.generators.providers.profile import ProviderProfile


@dataclass(frozen=True)
class LadderBranch:
    """One branch of the scenario-selection ladder, evaluated in order.

    Mirrors the historical ``if/elif`` chain exactly: the first branch whose threshold the
    roll falls under is chosen (``requires_credits`` branches are skipped unless credits are
    enabled); if that branch's ``min_remaining`` guard fails the row falls through to Usage.
    """

    kind: str  # "credit" | "tax" | "purchase" | "split" | "commitment"
    threshold: float
    requires_credits: bool = False
    min_remaining: int | None = None
    group: bool = False  # True if the builder returns multiple rows (commitment)


@dataclass(frozen=True)
class VersionAdapter:
    version: str  # "1.2" | "1.3"
    default_seed: int
    columns: tuple[str, ...]
    contract_commitment_columns: tuple[str, ...] | None
    ladder: tuple[LadderBranch, ...]
    # Keys copied verbatim from a commitment Purchase row onto every linked usage row.
    commitment_identity_keys: tuple[str, ...]
    emits_split_allocation: bool
    # Per-version field hooks (no-ops in 1.2):
    fill_version_identity: Callable[[dict, ProviderProfile], None]  # (row, profile)
    on_tax_row: Callable[[dict, str], None]  # (row, amount_str)
    on_credit_row: Callable[[dict, str], None]  # (row, negative_str)
    on_commit_usage: Callable[[dict, str, str, str], None]  # (usage, commit_id, contract_id, effective_str)
