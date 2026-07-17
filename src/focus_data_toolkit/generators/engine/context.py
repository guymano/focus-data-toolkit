"""Small value objects threaded through the engine row builders."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RowContext:
    """Identity carried from ``base_row`` into a scenario (the group's billing account)."""

    billing_id: str
    sub_id: str
    sub_name: str


@dataclass(frozen=True)
class ResourceRef:
    """Everything a provider's ``resource_id`` callable might need.

    Each provider reads only the fields it uses (AWS: region + billing account; Azure:
    subscription id + name; GCP: project id), so the callable signature stays uniform and
    the callables never draw from the RNG.
    """

    spec: object  # ServiceSpec (avoids a circular import with providers.profile)
    region_id: str
    region_name: str
    billing_id: str
    sub_id: str
    sub_name: str
    resource_name: str
