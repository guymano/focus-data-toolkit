"""Provider context — service provider vs host provider, determined per row.

The service provider (who sells the charge) and the host provider (whose infrastructure it
runs on) can differ: marketplaces, resellers, MSPs, and third-party services hosted on a
cloud. This context is therefore a **per-row** property; it must never be inferred once from
the first row of a file and applied globally.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderContext:
    """Who sells a charge (service) and whose infrastructure hosts it (host)."""

    service_provider_name: str
    host_provider_name: str

    @property
    def is_complete(self) -> bool:
        return bool(self.service_provider_name) and bool(self.host_provider_name)

    def as_dict(self) -> dict[str, str]:
        return {
            "service_provider_name": self.service_provider_name,
            "host_provider_name": self.host_provider_name,
        }


def provider_context_of_row(row: Mapping[str, str], source_version: str) -> ProviderContext:
    """Derive the provider context of a single Cost and Usage row.

    A 1.2 source expresses the service provider as ``ProviderName``; 1.3+ uses the
    ``ServiceProviderName`` / ``HostProviderName`` split (falling back to the deprecated
    ``ProviderName`` if a 1.3 export still carries it). A source that does not expose the
    underlying host gets ``host == service`` — FOCUS requires ``HostProviderName`` to
    match ``ServiceProviderName`` in that case. The deprecated ``PublisherName`` (the
    entity that *produced* the service) is never treated as a host. Absent values stay
    empty (UNAVAILABLE).
    """
    if source_version == "1.2":
        service = row.get("ProviderName", "") or ""
        host = service
    else:
        service = row.get("ServiceProviderName", "") or row.get("ProviderName", "") or ""
        host = row.get("HostProviderName", "") or service
    return ProviderContext(service.strip(), host.strip())


def distinct_provider_contexts(
    rows: Iterable[Mapping[str, str]], source_version: str
) -> list[ProviderContext]:
    """Return the distinct provider contexts across ``rows`` (deterministically ordered)."""
    seen: dict[tuple[str, str], ProviderContext] = {}
    for row in rows:
        ctx = provider_context_of_row(row, source_version)
        seen[(ctx.service_provider_name, ctx.host_provider_name)] = ctx
    return [seen[key] for key in sorted(seen)]


def representative_from_contexts(
    contexts: list[ProviderContext],
) -> tuple[ProviderContext, bool]:
    """Choose a representative provider from already-distinct contexts (see below)."""
    if not contexts:
        return ProviderContext("", ""), False
    # Prefer a usable representative: a fully complete context, else one with a non-empty
    # service provider, else the first. This avoids enriching with a blank provider (which
    # would fail the lint) when the same source also carries a complete provider elsewhere.
    chosen = (
        next((c for c in contexts if c.is_complete), None)
        or next((c for c in contexts if c.service_provider_name), None)
        or contexts[0]
    )
    return chosen, len(contexts) > 1


def representative_provider(
    rows: Iterable[Mapping[str, str]], source_version: str
) -> tuple[ProviderContext, bool]:
    """Return ``(context, ambiguous)`` — a usable representative and whether >1 exist.

    Used only where a single value must be chosen for enrichment (e.g. synthetic Contract
    Commitment, which the 1.3 source leaves without a provider). ``ambiguous`` is surfaced to
    the caller so the choice is never silent.
    """
    return representative_from_contexts(distinct_provider_contexts(rows, source_version))
