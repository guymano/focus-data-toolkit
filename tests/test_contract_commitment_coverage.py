"""Issue #67: the Contract Commitment dataset of a sample lists every commitment it applies.

FOCUS 1.3/1.4 say the Contract Commitment dataset "can be joined to the Cost and Usage
dataset through the use of Contract Commitment ID". No MUST requires every applied
commitment to be listed; requiring it here is the toolkit's interpretation, so that join
never loses a commitment the sample both purchases and applies.
"""
from __future__ import annotations

import json
import re

import pytest

from focus_data_toolkit.generators import PROVIDERS, get_generator
from focus_data_toolkit.generators.engine import serialize
from focus_data_toolkit.generators.versions.v1_2 import V12


def _applied(rows: list[dict[str, str]]) -> set[str]:
    return {
        element["ContractCommitmentId"]
        for row in rows if row["ContractApplied"]
        for element in json.loads(row["ContractApplied"])["Elements"]
    }


def _purchased(rows: list[dict[str, str]]) -> set[str]:
    """Commitments purchased in ``rows``: the Purchase rows whose ResourceId they are."""
    return {
        element["ContractCommitmentId"]
        for row in rows if row["ChargeCategory"] == "Purchase" and row["ContractApplied"]
        for element in json.loads(row["ContractApplied"])["Elements"]
        if element["ContractCommitmentId"] == row["ResourceId"]
    }


def _assert_covered(rows: list[dict[str, str]], contracts: list[dict[str, str]]) -> None:
    listed = {row["ContractCommitmentId"] for row in contracts}
    assert _applied(rows) <= listed
    assert _purchased(rows) <= listed
    # Not vacuous: the sample purchases and applies commitment discounts of its own,
    # beyond the negotiated terms every dataset lists.
    assert _purchased(rows) & _applied(rows)


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("credits", [False, True])
@pytest.mark.parametrize("seed", [None, 42])
def test_one_run_lists_every_applied_commitment(provider, credits, seed):
    module = get_generator(provider, "1.3")
    for size in (100, 1000):
        rows = module.generate_rows(size, seed, include_credits=credits)
        _assert_covered(rows, module.generate_contract_commitment_rows(
            size, seed, include_credits=credits))
        _assert_covered(rows, module.contract_commitment_rows_for(rows))


@pytest.mark.parametrize("provider", PROVIDERS)
def test_sample_from_a_larger_run_lists_every_applied_commitment(provider):
    # The #67 pattern: the first 100 rows of a 400-row run with credits.
    module = get_generator(provider, "1.3")
    sample = module.generate_rows(400, include_credits=True)[:100]
    _assert_covered(sample, module.contract_commitment_rows_for(sample))


def test_a_separate_run_misses_commitments_of_the_sample():
    # Why the sample's own rows are needed: since complete budget-aware groups (0.13.0),
    # the first 100 rows of a 400-row run differ from a 100-row run near the end, and
    # credits change the draws. A separately generated dataset then misses commitments.
    for provider in ("aws", "azure"):
        module = get_generator(provider, "1.3")
        sample = module.generate_rows(400, include_credits=True)[:100]
        separate = {row["ContractCommitmentId"]
                    for row in module.generate_contract_commitment_rows(100)}
        missing = _applied(sample) - separate
        assert missing, provider
        assert missing <= _purchased(sample), provider


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("credits", [False, True])
@pytest.mark.parametrize("seed", [None, 0, 7])
def test_rows_of_a_run_give_the_dataset_of_that_run(provider, credits, seed):
    module = get_generator(provider, "1.3")
    for size in (1, 7, 100, 1000):
        rows = module.generate_rows(size, seed, include_credits=credits)
        assert module.contract_commitment_rows_for(rows) == (
            module.generate_contract_commitment_rows(size, seed, include_credits=credits))


@pytest.mark.parametrize("provider", PROVIDERS)
def test_an_applied_commitment_without_its_purchase_is_refused(provider):
    module = get_generator(provider, "1.3")
    rows = module.generate_rows(100)
    commitment = sorted(_purchased(rows))[0]
    kept = [row for row in rows
            if not (row["ChargeCategory"] == "Purchase" and row["ResourceId"] == commitment)]
    with pytest.raises(ValueError, match="do not purchase: .*" + re.escape(commitment)):
        module.contract_commitment_rows_for(kept)


def test_focus_1_2_has_no_contract_commitment_dataset():
    module = get_generator("aws", "1.2")
    assert not hasattr(module, "contract_commitment_rows_for")
    with pytest.raises(ValueError, match="no Contract Commitment dataset"):
        serialize.contract_commitment_rows_for(
            module.generate_rows(10), profile=module.PROFILE, adapter=V12)
