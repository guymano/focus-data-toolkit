"""PR 6/7 semantic port: independent audit, boundaries, mutations and options."""
from __future__ import annotations

import copy
import json
import random
from decimal import Decimal

import pytest

from focus_data_toolkit.generators import get_generator
from focus_data_toolkit.generators.engine import scenarios_core
from scripts.sample_audit import audit, statistics


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
@pytest.mark.parametrize("version", ["1.2", "1.3"])
@pytest.mark.parametrize("credits", [False, True])
@pytest.mark.parametrize("seed", [0, 1, 42, None])
def test_independent_matrix(provider, version, credits, seed):
    module = get_generator(provider, version)
    for size in (*range(1, 13), 23, 24, 25, 100, 1000, 1001):
        rows = module.generate_rows(size, seed, include_credits=credits)
        cc = module.generate_contract_commitment_rows(size, seed, include_credits=credits) if version == "1.3" else None
        assert len(rows) == size
        audit(rows, provider, version, cc)
        if size == 1000 and seed is None:
            assert Decimal(statistics(rows, provider)["commitment_share"]) >= Decimal("0.05")


@pytest.mark.parametrize("budget", range(13))
def test_builders_own_their_budget(budget):
    m = get_generator("aws", "1.3")
    for builder in (scenarios_core.commitment_group, scenarios_core.split_allocation_group_rows):
        rows = builder(random.Random(42), 0, budget, m.PROFILE, m.ADAPTER)
        assert len(rows) <= budget
    assert len(scenarios_core.commitment_group(random.Random(42), 0, budget, m.PROFILE, m.ADAPTER)) % 4 == 0


def test_scheduler_refuses_an_oversized_group(monkeypatch):
    monkeypatch.setattr(scenarios_core, "commitment_group", lambda *a: [{}] * 10000)
    with pytest.raises(ValueError, match="budget"):
        get_generator("aws", "1.3").generate_rows(1000)


@pytest.fixture(scope="module")
def reference():
    m = get_generator("aws", "1.3")
    return m.generate_rows(1000), m.generate_contract_commitment_rows(1000)


@pytest.mark.parametrize("mutation", ["subscription", "tax", "sku", "unit", "parent", "purchase", "owner", "eligibility", "storage"])
def test_independent_audit_detects_corruption(reference, mutation):
    rows, contracts = copy.deepcopy(reference)
    if mutation == "subscription":
        r = next(r for r in rows if r["ChargeCategory"] == "Purchase" and not r["CommitmentDiscountId"])
        r["EffectiveCost"] = r["PricingCurrencyEffectiveCost"] = "0"
    elif mutation == "tax":
        r = next(r for r in rows if r["ChargeCategory"] == "Tax")
        r["ChargeDescription"] = "Tax for unrelated service"
    elif mutation == "sku":
        next(r for r in rows if r["SkuId"])["SkuId"] = "row-random-sku"
    elif mutation in ("unit", "parent", "eligibility"):
        r = next(r for r in rows if 'CC-USAGEMIN' in r.get("ContractApplied", ""))
        obj = json.loads(r["ContractApplied"])
        el = obj["Elements"][0]
        if mutation == "unit":
            el["ContractCommitmentAppliedUnit"] = "GB"
        elif mutation == "parent":
            el["ContractId"] = "wrong-parent"
        else:
            r["ServiceName"] = "AWSLambda"
        r["ContractApplied"] = json.dumps(obj)
    elif mutation == "purchase":
        rows.remove(next(r for r in rows if r["ChargeCategory"] == "Purchase" and r["CommitmentDiscountId"]))
    elif mutation == "owner":
        r = next(r for r in rows if r["ResourceId"] and not r["CommitmentDiscountId"])
        r["ResourceId"] = r["ResourceId"].replace(r["SubAccountId"], r["BillingAccountId"])
    else:
        r = next(r for r in rows if '"StorageClass"' in r["SkuPriceDetails"])
        r["SkuPriceDetails"] = r["SkuPriceDetails"].replace('"StorageClass"', '"x_StorageClass"')
    with pytest.raises((AssertionError, KeyError)):
        audit(rows, "aws", "1.3", contracts)


def test_no_cross_call_state_and_stable_offers():
    aws = get_generator("aws", "1.3")
    first = aws.generate_csv_bytes(1000, 42, include_credits=True)
    get_generator("azure", "1.2").generate_rows(1000, 0)
    assert first == aws.generate_csv_bytes(1000, 42, include_credits=True)
    a = aws.generate_rows(1000, 42)
    b = aws.generate_rows(1000, 1)
    # Audit together exercises the identity maps across seeds/accounts/categories;
    # tax references are local to each dataset, so check offers independently here.
    offers = {}
    for r in a + b:
        if r["SkuId"]:
            key = tuple(r[k] for k in ("ServiceName", "SkuMeter", "RegionId", "PricingUnit", "SkuPriceDetails"))
            assert offers.setdefault(key, r["SkuId"]) == r["SkuId"]


def test_default_contract_mix_and_parent_groups(reference):
    rows, contracts = reference
    ordinary = [r for r in rows if r["ChargeCategory"] == "Usage" and r["PricingCategory"] == "Standard" and not r["AllocatedMethodId"]]
    ratio = sum(bool(r["ContractApplied"]) for r in ordinary) / len(ordinary)
    assert .20 < ratio < .50  # coverage check on the fixed fixture, not every small sample
    discounts = [c for c in contracts if not c["ContractCommitmentId"].startswith("CC-")]
    parents = [c["ContractId"] for c in discounts]
    assert max(parents.count(p) for p in parents) == 3
