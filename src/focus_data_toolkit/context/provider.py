"""Provider context — service provider vs host provider, determined per row.

The service provider (who sells the charge) and the host provider (whose infrastructure it
runs on) can differ: marketplaces, resellers, MSPs, and third-party services hosted on a
cloud. This context is therefore a **per-row** property; it must never be inferred once from
the first row of a file and applied globally.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

PROVIDER_ROLES: tuple[str, ...] = ("csp", "msp")


class ProviderRoleError(ValueError):
    """An invalid :class:`ProviderRole` declaration."""


@dataclass(frozen=True)
class ProviderRole:
    """Who issued a FOCUS 1.2 source, as declared by the caller (FOCUS 1.2 does not say it).

    In FOCUS 1.2, a ``PublisherName`` that differs from ``ProviderName`` means two opposite
    things (FOCUS 1.2 appendix "Origination of Cost Data"): a cloud marketplace purchase
    (Provider = cloud provider, Publisher = the seller; scenarios 3.1-3.3) or cloud services
    bought through an MSP (Provider = MSP, Publisher = cloud provider; scenario 2.1). FOCUS 1.4
    makes the seller the Service Provider in the first case and the MSP in the second
    (ServiceProviderName notes; appendix "Participating Entity Identification" 2.1-2.2 and
    3.1.1-3.3.2), and a row alone cannot tell them apart:

    * ``csp`` - the file comes from a cloud provider: a row is a Marketplace charge when its
      ``PublisherName`` is neither its ``ProviderName`` nor one of ``first_party_publishers``
      (the provider's own publisher names, e.g. "Microsoft" next to "Microsoft Azure"), and
      its Service Provider is that ``PublisherName``;
    * ``msp`` - the file comes from an MSP or reseller: the Service Provider is ``ProviderName``;
    * none - ``ProviderName`` is kept (the converter reports such rows, ``FDT-CTX-005``).

    Only the official FOCUS 1.2 columns are read; provider-specific ``x_`` columns are not.
    """

    role: str | None = None
    first_party_publishers: frozenset[str] = frozenset()  # case-folded

    @classmethod
    def declare(
        cls, role: str | None = None, first_party_publishers: Iterable[str] = ()
    ) -> ProviderRole:
        """Validate a caller's declaration (:class:`ProviderRoleError` on an invalid one)."""
        names = frozenset(n.strip().casefold() for n in first_party_publishers if n.strip())
        if role is not None and role not in PROVIDER_ROLES:
            raise ProviderRoleError(
                f"unknown provider role {role!r}; expected one of {', '.join(PROVIDER_ROLES)}"
            )
        if names and role != "csp":
            raise ProviderRoleError(
                "first-party publisher names apply only to the provider role 'csp' (a file "
                "issued by the cloud provider)"
            )
        return cls(role, names)

    def publisher_differs(self, row: Mapping[str, str]) -> bool:
        """Whether a 1.2 row names a publisher other than its provider and first-party names."""
        publisher = (row.get("PublisherName") or "").strip().casefold()
        provider = (row.get("ProviderName") or "").strip().casefold()
        return bool(publisher) and publisher != provider and (
            publisher not in self.first_party_publishers
        )

    def describe(self) -> str:
        """The declaration as recorded in the manifest provenance."""
        if self.role != "csp" or not self.first_party_publishers:
            return f"declared provider role {self.role!r}"
        names = ", ".join(sorted(self.first_party_publishers))
        return f"declared provider role 'csp'; first-party publishers: {names}"


def service_provider_of_1_2_row(row: Mapping[str, str], declared: ProviderRole) -> str:
    """``ServiceProviderName`` (and so ``HostProviderName``) of a FOCUS 1.2 row.

    ``ProviderName`` by default (its 1.3 replacement); ``PublisherName`` for a Marketplace row
    of a file issued by a cloud provider (``csp``). Source text is kept as is.
    """
    if declared.role == "csp" and declared.publisher_differs(row):
        return row.get("PublisherName", "") or ""
    return row.get("ProviderName", "") or ""


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


def provider_context_of_row(
    row: Mapping[str, str], source_version: str, provider_role: ProviderRole | None = None
) -> ProviderContext:
    """Derive the provider context of a single Cost and Usage row.

    A 1.2 source expresses the service provider as ``ProviderName``, or as ``PublisherName``
    on the Marketplace rows of a file declared as issued by a cloud provider
    (``provider_role``, see :class:`ProviderRole`); 1.3+ uses the
    ``ServiceProviderName`` / ``HostProviderName`` split (falling back to the deprecated
    ``ProviderName`` if a 1.3 export still carries it). A source that does not expose the
    underlying host gets ``host == service`` — FOCUS requires ``HostProviderName`` to
    match ``ServiceProviderName`` in that case. The deprecated ``PublisherName`` is never
    treated as a host. Absent values stay empty (UNAVAILABLE).
    """
    if source_version == "1.2":
        service = service_provider_of_1_2_row(row, provider_role or ProviderRole())
        host = service
    else:
        service = row.get("ServiceProviderName", "") or row.get("ProviderName", "") or ""
        host = row.get("HostProviderName", "") or service
    return ProviderContext(service.strip(), host.strip())


def distinct_provider_contexts(
    rows: Iterable[Mapping[str, str]],
    source_version: str,
    provider_role: ProviderRole | None = None,
) -> list[ProviderContext]:
    """Return the distinct provider contexts across ``rows`` (deterministically ordered)."""
    seen: dict[tuple[str, str], ProviderContext] = {}
    for row in rows:
        ctx = provider_context_of_row(row, source_version, provider_role)
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
    the caller so the choice is never silent. No provider role applies here: for a 1.2 source
    the representative is the provider that issued the file (``ProviderName``, "the entity
    that made the resources or services available for purchase"), from which commitments
    are bought, never a Marketplace seller.
    """
    return representative_from_contexts(distinct_provider_contexts(rows, source_version))
