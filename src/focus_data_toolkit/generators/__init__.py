"""Deterministic, provider-realistic FOCUS 1.2/1.3 source generators.

Each ``generate_<provider>_focus_<version>`` module is a thin shim binding a provider profile
to a version adapter (see :mod:`focus_data_toolkit.generators.engine`); a given ``(rows, seed)``
pair always produces the same CSV. All modules expose ``generate_csv_bytes(rows, seed)``; the
1.3 modules additionally expose ``generate_contract_commitment_csv_bytes(rows, seed)``.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType, SimpleNamespace

PROVIDERS: tuple[str, ...] = ("aws", "azure", "gcp")
FOCUS_VERSIONS: tuple[str, ...] = ("1.2", "1.3")

# In-process registry consulted first by get_generator. Lets tests register an extra provider
# profile (a "fake" cloud) without adding a module file, proving the engine is provider-agnostic.
_REGISTRY: dict[tuple[str, str], ModuleType | SimpleNamespace] = {}


def register_generator(provider: str, focus_version: str, api: ModuleType | SimpleNamespace) -> None:
    """Register an in-process generator (e.g. from ``engine._shim.build_module_api``).

    ``api`` must expose ``generate_csv_bytes`` (and, for a 1.3-style adapter,
    ``generate_contract_commitment_csv_bytes``). Test-oriented seam; production code uses the
    six shipped modules via :func:`get_generator`.
    """
    _REGISTRY[(provider, focus_version)] = api


def unregister_generator(provider: str, focus_version: str) -> None:
    """Remove a previously registered in-process generator (no-op if absent)."""
    _REGISTRY.pop((provider, focus_version), None)


def get_generator(provider: str, focus_version: str) -> ModuleType | SimpleNamespace:
    """Return the generator for ``provider`` and ``focus_version``.

    Registered in-process generators win over the shipped modules; otherwise the matching
    ``generate_<provider>_focus_<version>`` shim module is imported.
    """
    if (provider, focus_version) in _REGISTRY:
        return _REGISTRY[(provider, focus_version)]
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; expected one of {PROVIDERS}")
    if focus_version not in FOCUS_VERSIONS:
        raise ValueError(
            f"unsupported FOCUS version {focus_version!r}; expected one of {FOCUS_VERSIONS}"
        )
    suffix = focus_version.replace(".", "_")
    return import_module(f"focus_data_toolkit.generators.generate_{provider}_focus_{suffix}")


__all__ = [
    "FOCUS_VERSIONS",
    "PROVIDERS",
    "get_generator",
    "register_generator",
    "unregister_generator",
]
