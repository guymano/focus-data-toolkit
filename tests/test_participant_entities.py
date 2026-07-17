"""FOCUS 1.2 participant-entity migration (P0 correctness fix).

Per the official FOCUS 1.4 ``HostProviderName`` rules, when a source does not expose
the underlying infrastructure host the value MUST match ``ServiceProviderName``.
A 1.2 source never exposes the host, so ``HostProviderName`` derives from the
``ServiceProviderName`` value (itself derived from ``ProviderName``, its 1.3
replacement). The deprecated ``PublisherName`` — the entity that *produced* the
service — must never be treated as a host equivalent: a SaaS publisher's product
hosted on a cloud has ``PublisherName != HostProviderName``.
"""

from __future__ import annotations

from focus_data_toolkit.context import provider_context_of_row
from focus_data_toolkit.convert import convert_to_focus_1_4
from focus_data_toolkit.convert.cost_and_usage import (
    convert_cost_and_usage_row,
    cost_and_usage_provenance,
)
from focus_data_toolkit.provenance import Lineage

# A 1.2 marketplace/SaaS row: the publisher (who produced the service) differs from
# the provider (who made it available). The publisher is NOT the infrastructure host.
MARKETPLACE_1_2 = {
    "ProviderName": "AWS",
    "PublisherName": "Datadog",
    "InvoiceIssuerName": "AWS",
    "BillingCurrency": "USD",
    "BilledCost": "10.00",
    "EffectiveCost": "10.00",
}


def test_1_2_host_provider_matches_service_provider_not_publisher():
    row = convert_cost_and_usage_row(MARKETPLACE_1_2, "1.2")
    assert row["ServiceProviderName"] == "AWS"
    assert row["HostProviderName"] == "AWS"
    assert row["HostProviderName"] == row["ServiceProviderName"]
    assert row["HostProviderName"] != MARKETPLACE_1_2["PublisherName"]


def test_1_2_participant_entity_lineage_is_derived_never_renamed():
    rules = cost_and_usage_provenance(
        MARKETPLACE_1_2.keys(), "1.2", invoice_detail_linked=False
    )
    assert rules["ServiceProviderName"].lineage is Lineage.DERIVED
    assert rules["HostProviderName"].lineage is Lineage.DERIVED
    # The mapping documents the spec rule and never claims a faithful rename.
    assert "ServiceProviderName" in (rules["HostProviderName"].source or "")
    for rule in (rules["ServiceProviderName"], rules["HostProviderName"]):
        assert rule.lineage is not Lineage.RENAMED
        assert "PublisherName" not in (rule.source or "")


def test_1_2_provider_context_host_matches_service():
    ctx = provider_context_of_row(MARKETPLACE_1_2, "1.2")
    assert ctx.service_provider_name == "AWS"
    assert ctx.host_provider_name == "AWS"


def test_1_3_provider_context_falls_back_to_service_not_publisher():
    # A 1.3 export still carrying the deprecated columns but no HostProviderName:
    # the host falls back to the service provider, never to the publisher.
    row = {"ServiceProviderName": "Datadog", "PublisherName": "Datadog Inc.",
           "ProviderName": "AWS"}
    ctx = provider_context_of_row(row, "1.3")
    assert ctx.host_provider_name == "Datadog"


def test_1_2_end_to_end_conversion_never_reads_publisher_into_host(source_tables):
    cau, _ = source_tables[("aws", "1.2")]
    tampered = [dict(r, PublisherName="Some SaaS Publisher") for r in cau]
    result = convert_to_focus_1_4(tampered, source_version="1.2", validate=False)
    for out in result.datasets["Cost and Usage"]:
        assert out["HostProviderName"] == out["ServiceProviderName"]
        assert out["HostProviderName"] != "Some SaaS Publisher"
