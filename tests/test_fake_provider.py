"""Adding a provider is one profile value — no engine change (P2-A extensibility goal).

A fourth "fake" cloud is defined in-process (a data literal plus a few small callables),
registered, and driven through the same engine. If the engine had provider logic baked in,
this could not work.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from types import SimpleNamespace

import pytest

from focus_data_toolkit.generators import get_generator, register_generator, unregister_generator
from focus_data_toolkit.generators._shim import build_module_api
from focus_data_toolkit.generators.engine.determinism import hexid
from focus_data_toolkit.generators.providers.profile import (
    CommitmentModel,
    ProviderProfile,
    ServiceSpec,
)
from focus_data_toolkit.generators.versions import V12, V13

FAKE = ProviderProfile(
    key="fake",
    provider_name="FakeCloud",
    publisher_name="FakeCloud",
    service_provider_name="FakeCloud",
    host_provider_name="FakeCloud",
    invoice_issuer_name="FakeCloud",
    billing_account_type="Fake Account",
    sub_account_type="Fake Project",
    split_allocation_description="Shared FakeCompute host cost allocated to workload",
    tag_keys=("Environment", "CostCenter", "Owner"),
    invoice_id=lambda billing_id: f"FINV-{billing_id[-4:]}",
    services=(
        ServiceSpec(
            "FakeCompute", "Compute", "Virtual Machines", "Fake Instance",
            "Compute", "Hours", "Fake VM hours", Decimal("0.10"),
            Decimal("1"), Decimal("1"), "fk-", "hourly", True, True,
            id_fields={"kind": "vm"},
            sku_details={"InstanceType": "fake.large"},
        ),
        ServiceSpec(
            "FakeStore", "Storage", "Object Storage", "Fake Bucket",
            "Storage", "GB-Months", "Fake object storage", Decimal("0.02"),
            Decimal("10"), Decimal("100"), "fs-", "monthly", False, False,
            id_fields={"kind": "bucket"},
            sku_details={"StorageClass": "Standard"},
        ),
    ),
    regions=(("fake-1", "Fake Region 1", ("fake-1a", "fake-1b")),),
    billing_accounts=(("900000000001", "FakeCorp Payer"),),
    sub_accounts=(("900000000011", "fake-prod"),),
    resource_id=lambda ref: f"fake://{ref.sub_id}/{ref.spec.id_fields['kind']}/{ref.resource_name}",
    resource_name=lambda rng, spec: f"{spec.name_prefix}{hexid(rng, 8)}",
    committed_resource_name=lambda rng, spec, k: f"{spec.name_prefix}{k:04d}{hexid(rng, 6)}",
    sku_id=lambda rng, spec: f"FSKU-{hexid(rng, 6)}",
    sku_price_id=lambda rng: f"FSP-{hexid(rng, 8)}",
    allocated_resource_id=lambda rng, region_id, ctx, workload: f"fake://{ctx.sub_id}/wl/{workload}-{hexid(rng, 6)}",
    commitment=CommitmentModel(
        commit_id_before_base_row=True,
        commit_id=lambda rng, region_id, sub_id, spend_based: f"fake-commit-{hexid(rng, 12)}",
        commit_resource_name=lambda rng, spend_based: f"commit-{hexid(rng, 10)}",
        purchase_sku_id=lambda rng: f"FSKU-COMMIT-{hexid(rng, 6)}",
        purchase_sku_details=lambda spend_based: '{"x_Term":"1yr"}',
        purchase_description=lambda commit_type: f"{commit_type} commitment purchase",
        commit_name=lambda spend_based: "FakeCommit-1yr",
        commit_type=lambda spend_based: "Fake Commitment",
        commit_category=lambda spend_based: "Spend" if spend_based else "Usage",
        commit_unit=lambda spend_based: "USD" if spend_based else "Hours",
    ),
)


def _rows(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode())))


@pytest.fixture
def fake_registered():
    register_generator("fake", "1.2", SimpleNamespace(**build_module_api(FAKE, V12)))
    register_generator("fake", "1.3", SimpleNamespace(**build_module_api(FAKE, V13)))
    try:
        yield
    finally:
        unregister_generator("fake", "1.2")
        unregister_generator("fake", "1.3")


@pytest.mark.parametrize(("version", "columns"), [("1.2", 57), ("1.3", 65)])
def test_fake_provider_generates_and_is_deterministic(fake_registered, version, columns):
    module = get_generator("fake", version)
    data = module.generate_csv_bytes(30, 7, include_credits=True)
    assert data == module.generate_csv_bytes(30, 7, include_credits=True)  # determinism holds
    rows = _rows(data)
    assert rows and all(len(row) == columns for row in rows)
    assert all(row["ProviderName"] == "FakeCloud" for row in rows)


def test_fake_provider_needed_no_new_module(fake_registered):
    # The fake provider is a value, registered in-process: proof the engine is provider-agnostic.
    cc = _rows(get_generator("fake", "1.3").generate_contract_commitment_csv_bytes(120, 4))
    assert all(row["ContractCommitmentId"].startswith("fake-commit-") for row in cc)
