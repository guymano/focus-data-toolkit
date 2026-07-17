"""Cross-provider consistency for the shared generation engine.

Every provider profile, run through the same engine, must satisfy the same structural
invariants — proving the engine is genuinely provider-agnostic and that the profiles stay
realistic and complete.
"""

from __future__ import annotations

import csv
import io

import pytest

from focus_data_toolkit.generators import PROVIDERS, get_generator
from focus_data_toolkit.generators.providers import PROFILES
from focus_data_toolkit.generators.versions import V12, V13


def _rows(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode())))


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(("version", "adapter"), [("1.2", V12), ("1.3", V13)])
def test_columns_match_adapter(provider, version, adapter):
    rows = _rows(get_generator(provider, version).generate_csv_bytes(80, 3))
    assert rows, "generator produced no rows"
    for row in rows:
        assert tuple(row.keys()) == adapter.columns


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("version", ["1.2", "1.3"])
def test_common_charge_categories_and_wellformed(provider, version):
    rows = _rows(get_generator(provider, version).generate_csv_bytes(200, 5))
    categories = {row["ChargeCategory"] for row in rows}
    # Usage, Purchase and Tax are reliably reachable in a 200-row sample.
    assert {"Usage", "Purchase", "Tax"} <= categories
    for row in rows:
        assert row["ProviderName"]  # identity always present
        assert row["BillingCurrency"] == "USD"
        assert row["BilledCost"] != "" or row["ChargeCategory"] == "Purchase"


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("version", ["1.2", "1.3"])
def test_credit_path_reachable(provider, version):
    # Credit is an opt-in ~5% branch; assert it is producible (deterministically) across seeds.
    module = get_generator(provider, version)
    found = any(
        "Credit" in {r["ChargeCategory"] for r in _rows(module.generate_csv_bytes(120, seed, include_credits=True))}
        for seed in range(1, 25)
    )
    assert found


@pytest.mark.parametrize("provider", PROVIDERS)
def test_contract_commitment_join_key(provider):
    module = get_generator(provider, "1.3")
    cu = _rows(module.generate_csv_bytes(150, 11))
    cc = _rows(module.generate_contract_commitment_csv_bytes(150, 11))
    purchase_ids = {r["CommitmentDiscountId"] for r in cu if r["ChargeCategory"] == "Purchase" and r["CommitmentDiscountId"]}
    commitment_ids = {r["ContractCommitmentId"] for r in cc}
    assert commitment_ids, "expected at least one commitment"
    # Every Contract Commitment row is joinable to a Cost and Usage purchase, and vice versa.
    assert commitment_ids == purchase_ids


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("version", ["1.2", "1.3"])
def test_deterministic(provider, version):
    module = get_generator(provider, version)
    assert module.generate_csv_bytes(40, 9) == module.generate_csv_bytes(40, 9)


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(("version", "adapter"), [("1.2", V12), ("1.3", V13)])
def test_module_api_defaults(provider, version, adapter):
    # The historical module API had default arguments: generate_rows() /
    # generate_rows(rows=N) / generate_csv_bytes() must work without an explicit seed.
    module = get_generator(provider, version)
    assert len(module.generate_rows()) == 1000
    assert len(module.generate_rows(rows=12)) == 12
    # No-arg output uses the adapter's default seed and is byte-stable.
    assert module.generate_csv_bytes() == module.generate_csv_bytes(1000, adapter.default_seed)


def test_profiles_have_distinct_identities():
    names = {p.provider_name for p in PROFILES.values()}
    assert len(names) == len(PROFILES) == 3
    for profile in PROFILES.values():
        assert len(profile.services) == 8  # realistic service diversity preserved
        assert profile.commitment_service is profile.services[0]
