"""Deterministic, provider-realistic FOCUS 1.2/1.3 source generators.

Each module is self-contained (standard library only) and byte-reproducible:
a given ``(rows, seed)`` pair always produces the same CSV. All modules expose
``generate_csv_bytes(rows, seed)``; the 1.3 modules additionally expose
``generate_contract_commitment_csv_bytes(rows, seed)``.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

PROVIDERS: tuple[str, ...] = ("aws", "azure", "gcp")
FOCUS_VERSIONS: tuple[str, ...] = ("1.2", "1.3")


def get_generator(provider: str, focus_version: str) -> ModuleType:
    """Return the generator module for ``provider`` and ``focus_version``."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; expected one of {PROVIDERS}")
    if focus_version not in FOCUS_VERSIONS:
        raise ValueError(
            f"unsupported FOCUS version {focus_version!r}; expected one of {FOCUS_VERSIONS}"
        )
    suffix = focus_version.replace(".", "_")
    return import_module(f"focus_data_toolkit.generators.generate_{provider}_focus_{suffix}")


__all__ = ["FOCUS_VERSIONS", "PROVIDERS", "get_generator"]
