"""Bind a (ProviderProfile, VersionAdapter) pair to the historical per-module public API.

Each ``generate_<provider>_focus_<version>.py`` module is a ~10-line shim that calls
:func:`build_module_api` and publishes the result, so ``COLUMNS`` / ``generate_csv_bytes`` /
``generate_rows`` / ``main`` / the ``python -m`` entry point keep working unchanged.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from focus_data_toolkit.generators.engine import serialize
from focus_data_toolkit.generators.engine.ladder import generate_rows
from focus_data_toolkit.generators.providers.profile import ProviderProfile
from focus_data_toolkit.generators.versions.adapter import VersionAdapter


def build_module_api(profile: ProviderProfile, adapter: VersionAdapter) -> dict[str, Any]:
    """Return the names a generator shim module must expose, bound to (profile, adapter).

    ``Any``-valued on purpose: the entries are heterogeneous module attributes (tuples,
    callables, profile objects) that shim modules publish verbatim via ``globals().update``.
    """
    api: dict[str, Any] = {
        "COLUMNS": adapter.columns,
        "DEFAULT_ROWS": serialize.DEFAULT_ROWS,
        "DEFAULT_SEED": adapter.default_seed,
        "PROFILE": profile,
        "ADAPTER": adapter,
        "generate_rows": partial(generate_rows, profile=profile, adapter=adapter),
        "generate_csv_bytes": partial(serialize.generate_csv_bytes, profile=profile, adapter=adapter),
        "main": partial(serialize.main, profile=profile, adapter=adapter),
    }
    if adapter.contract_commitment_columns is not None:
        api["CONTRACT_COMMITMENT_COLUMNS"] = adapter.contract_commitment_columns
        api["generate_contract_commitment_rows"] = partial(
            serialize.generate_contract_commitment_rows, profile=profile, adapter=adapter
        )
        api["generate_contract_commitment_csv_bytes"] = partial(
            serialize.generate_contract_commitment_csv_bytes, profile=profile, adapter=adapter
        )
    return api
