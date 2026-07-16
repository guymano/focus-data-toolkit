"""ContractApplied model / parser / migration tests.

Cases are hand-authored from contractapplied.md @ v1.3 / v1.4 (see
tests/fixtures/official/SOURCES.md), independent of the toolkit's generators.
"""

from __future__ import annotations

import json

import pytest

from focus_data_toolkit.convert.contract_applied import (
    ContractAppliedError,
    migrate_1_3_to_1_4,
    parse,
    to_json,
)

# A valid FOCUS 1.3 ContractApplied (uppercase ID keys; metrics are JSON numbers).
VALID_1_3 = (
    '{"Elements":[{"ContractID":"CONTRACT-1","ContractCommitmentID":"sp-1",'
    '"ContractCommitmentAppliedCost":0.0641,'
    '"ContractCommitmentAppliedQuantity":1.0000,'
    '"ContractCommitmentAppliedUnit":"Hours","x_Note":"n"}]}'
)


def test_parse_valid_1_3():
    ca = parse(VALID_1_3, version="1.3")
    assert len(ca.elements) == 1
    el = ca.elements[0]
    assert el.contract_id == "CONTRACT-1"
    assert el.contract_commitment_id == "sp-1"
    assert el.applied_cost == "0.0641"
    assert el.custom == {"x_Note": "n"}


def test_migrate_1_3_to_1_4_recases_ids_and_keeps_numbers():
    out = migrate_1_3_to_1_4(VALID_1_3)
    # IDs re-cased ID -> Id, old casing gone.
    assert '"ContractId":"CONTRACT-1"' in out
    assert '"ContractCommitmentId":"sp-1"' in out
    assert "ContractID" not in out and "ContractCommitmentID" not in out
    # Metric keys unchanged; cost/quantity remain JSON numbers (not quoted).
    assert '"ContractCommitmentAppliedCost":0.0641' in out
    assert '"ContractCommitmentAppliedQuantity":1.0000' in out
    assert '"x_Note":"n"' in out
    # Re-parseable as 1.4.
    assert parse(out, version="1.4").elements[0].applied_unit == "Hours"


def test_numeric_metrics_are_json_numbers():
    obj = json.loads(migrate_1_3_to_1_4(VALID_1_3))
    el = obj["Elements"][0]
    assert isinstance(el["ContractCommitmentAppliedCost"], (int, float))
    assert isinstance(el["ContractCommitmentAppliedQuantity"], (int, float))
    assert isinstance(el["ContractCommitmentAppliedUnit"], str)


def test_to_json_roundtrip_1_4():
    ca = parse(migrate_1_3_to_1_4(VALID_1_3), version="1.4")
    assert parse(to_json(ca, version="1.4"), version="1.4") == ca


@pytest.mark.parametrize(
    ("bad", "needle"),
    [
        ("not json", "invalid JSON"),
        ('{"Elements":[]}', "must not be empty"),
        ('{"Elements":{}}', "'Elements' array"),
        ('{"x_Foo":1}', "'Elements' array"),
        ('{"Elements":[1]}', "must be an object"),
        ('{"Elements":[{"ContractCommitmentID":"c","ContractCommitmentAppliedCost":1}]}', "ContractID"),
        ('{"Elements":[{"ContractID":"c","ContractCommitmentAppliedCost":1}]}', "ContractCommitmentID"),
        ('{"Elements":[{"ContractID":"c","ContractCommitmentID":"x"}]}', "must provide"),
        (
            '{"Elements":[{"ContractID":"c","ContractCommitmentID":"x",'
            '"ContractCommitmentAppliedQuantity":1}]}',
            "ContractCommitmentAppliedUnit when",
        ),
        (
            '{"Elements":[{"ContractID":"c","ContractCommitmentID":"x",'
            '"ContractCommitmentAppliedCost":1,"Bogus":"y"}]}',
            "must be prefixed with 'x_'",
        ),
        (
            '{"Elements":[{"ContractID":"c","ContractCommitmentID":"x",'
            '"ContractCommitmentAppliedCost":"abc"}]}',
            "numeric",
        ),
        ('{"Bogus":1,"Elements":[{"ContractID":"c","ContractCommitmentID":"x",'
         '"ContractCommitmentAppliedCost":1}]}', "top-level custom key"),
    ],
)
def test_parse_rejects_invalid(bad, needle):
    with pytest.raises(ContractAppliedError) as exc:
        parse(bad, version="1.3")
    assert needle in str(exc.value)


def test_parse_rejects_duplicate_keys():
    dup = (
        '{"Elements":[{"ContractID":"c","ContractID":"d","ContractCommitmentID":"x",'
        '"ContractCommitmentAppliedCost":1}]}'
    )
    with pytest.raises(ContractAppliedError, match="duplicate JSON key"):
        parse(dup, version="1.3")
