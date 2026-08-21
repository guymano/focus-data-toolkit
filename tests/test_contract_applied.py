"""ContractApplied model / parser / migration tests.

Cases are hand-authored from contractapplied.md and contractappliedobjectschema.json
@ v1.3 / v1.4 (see tests/fixtures/official/SOURCES.md), independent of the toolkit's
generators.
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

# A valid FOCUS 1.3 ContractApplied in the legacy pre-erratum uppercase-ID casing
# (metrics are JSON numbers). FOCUS erratum #3 corrected the identifier keys to the
# Id casing; the legacy casing must stay parseable for files produced before it.
# 1.3 permits cost AND quantity+unit simultaneously; 1.4's oneOf does not.
VALID_1_3_BOTH = (
    '{"Elements":[{"ContractID":"CONTRACT-1","ContractCommitmentID":"sp-1",'
    '"ContractCommitmentAppliedCost":0.0641,'
    '"ContractCommitmentAppliedQuantity":1.0000,'
    '"ContractCommitmentAppliedUnit":"Hours","x_Note":"n"}]}'
)
VALID_1_3_COST_ONLY = (
    '{"Elements":[{"ContractID":"CONTRACT-1","ContractCommitmentID":"sp-1",'
    '"ContractCommitmentAppliedCost":0.0641}]}'
)
VALID_1_3_QTY_ONLY = (
    '{"Elements":[{"ContractID":"CONTRACT-1","ContractCommitmentID":"sp-1",'
    '"ContractCommitmentAppliedQuantity":1.0000,'
    '"ContractCommitmentAppliedUnit":"Hours"}]}'
)


def test_parse_valid_1_3():
    ca = parse(VALID_1_3_BOTH, version="1.3")
    assert len(ca.elements) == 1
    el = ca.elements[0]
    assert el.contract_id == "CONTRACT-1"
    assert el.contract_commitment_id == "sp-1"
    assert el.applied_cost == "0.0641"
    assert el.applied_quantity == "1.0000"
    assert el.custom == {"x_Note": "n"}


def test_migrate_recases_ids_and_keeps_numbers():
    out = migrate_1_3_to_1_4(VALID_1_3_COST_ONLY)
    # IDs re-cased ID -> Id, old casing gone.
    assert '"ContractId":"CONTRACT-1"' in out
    assert '"ContractCommitmentId":"sp-1"' in out
    assert "ContractID" not in out and "ContractCommitmentID" not in out
    # Cost remains a JSON number (not quoted).
    assert '"ContractCommitmentAppliedCost":0.0641' in out
    assert parse(out, version="1.4").elements[0].applied_cost == "0.0641"


def test_migrate_both_branches_keeps_cost_and_preserves_qty_as_custom():
    # 1.4 oneOf: an element may not carry both metric branches. The migration keeps
    # cost and preserves quantity/unit losslessly as x_ custom keys.
    out = migrate_1_3_to_1_4(VALID_1_3_BOTH)
    obj = json.loads(out)
    el = obj["Elements"][0]
    assert isinstance(el["ContractCommitmentAppliedCost"], (int, float))
    assert "ContractCommitmentAppliedQuantity" not in el
    assert "ContractCommitmentAppliedUnit" not in el
    assert isinstance(el["x_ContractCommitmentAppliedQuantity"], (int, float))
    assert el["x_ContractCommitmentAppliedUnit"] == "Hours"
    assert el["x_Note"] == "n"
    # The migrated value satisfies the 1.4 parser (hence the linter).
    assert parse(out, version="1.4").elements[0].applied_quantity is None


def test_migrate_quantity_only_branch_unchanged():
    out = migrate_1_3_to_1_4(VALID_1_3_QTY_ONLY)
    el = json.loads(out)["Elements"][0]
    assert "ContractCommitmentAppliedCost" not in el
    assert isinstance(el["ContractCommitmentAppliedQuantity"], (int, float))
    assert el["ContractCommitmentAppliedUnit"] == "Hours"
    parse(out, version="1.4")


def test_parse_1_4_rejects_both_metric_branches():
    both_1_4 = (
        '{"Elements":[{"ContractId":"c","ContractCommitmentId":"x",'
        '"ContractCommitmentAppliedCost":1,'
        '"ContractCommitmentAppliedQuantity":2,"ContractCommitmentAppliedUnit":"Hours"}]}'
    )
    with pytest.raises(ContractAppliedError, match="oneOf"):
        parse(both_1_4, version="1.4")
    # The same shape is legal in 1.3 (at-least-one semantics).
    parse(both_1_4.replace('"ContractId"', '"ContractID"')
          .replace('"ContractCommitmentId"', '"ContractCommitmentID"'), version="1.3")


def test_parse_rejects_quoted_numeric_strings():
    quoted = (
        '{"Elements":[{"ContractID":"c","ContractCommitmentID":"x",'
        '"ContractCommitmentAppliedCost":"1"}]}'
    )
    with pytest.raises(ContractAppliedError, match="not a quoted string"):
        parse(quoted, version="1.3")


def test_to_json_roundtrip_1_4():
    ca = parse(migrate_1_3_to_1_4(VALID_1_3_BOTH), version="1.4")
    assert parse(to_json(ca, version="1.4"), version="1.4") == ca


def test_to_json_1_4_refuses_both_branches():
    ca = parse(VALID_1_3_BOTH, version="1.3")
    with pytest.raises(ContractAppliedError, match="oneOf"):
        to_json(ca, version="1.4")
    # 1.3 serialization of the same model is fine.
    assert '"ContractCommitmentAppliedQuantity":1.0000' in to_json(ca, version="1.3")


@pytest.mark.parametrize(
    ("bad", "needle"),
    [
        ("not json", "invalid JSON"),
        ('{"Elements":[]}', "must not be empty"),
        ('{"Elements":{}}', "'Elements' array"),
        ('{"x_Foo":1}', "'Elements' array"),
        ('{"Elements":[1]}', "must be an object"),
        ('{"Elements":[{"ContractCommitmentID":"c","ContractCommitmentAppliedCost":1}]}', "ContractId"),
        ('{"Elements":[{"ContractID":"c","ContractCommitmentAppliedCost":1}]}', "ContractCommitmentId"),
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
            "JSON number",
        ),
        (
            '{"Elements":[{"ContractID":123,"ContractCommitmentID":"x",'
            '"ContractCommitmentAppliedCost":1}]}',
            "non-empty string",
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


# --- FOCUS erratum #3: canonical Id casing in 1.3 ---------------------------------

VALID_1_3_CANONICAL = (
    '{"Elements":[{"ContractId":"CONTRACT-1","ContractCommitmentId":"sp-1",'
    '"ContractCommitmentAppliedCost":0.0641}]}'
)


def test_parse_1_3_accepts_canonical_erratum_casing():
    sink: set[str] = set()
    ca = parse(VALID_1_3_CANONICAL, version="1.3", legacy_sink=sink)
    el = ca.elements[0]
    assert el.contract_id == "CONTRACT-1"
    assert el.contract_commitment_id == "sp-1"
    assert sink == set()  # canonical casing is not a legacy encounter


def test_parse_1_3_records_legacy_casing_in_sink():
    sink: set[str] = set()
    parse(VALID_1_3_COST_ONLY, version="1.3", legacy_sink=sink)
    assert sink == {"ContractID", "ContractCommitmentID"}


def test_parse_1_3_rejects_mixed_casing_in_one_element():
    ambiguous = (
        '{"Elements":[{"ContractId":"c","ContractID":"d","ContractCommitmentId":"x",'
        '"ContractCommitmentAppliedCost":1}]}'
    )
    with pytest.raises(ContractAppliedError, match="only the canonical casing"):
        parse(ambiguous, version="1.3")


def test_parse_1_4_rejects_legacy_casing():
    # 1.4 never had the uppercase casing; there it is a plain unknown custom key.
    with pytest.raises(ContractAppliedError, match="must be prefixed with 'x_'"):
        parse(VALID_1_3_COST_ONLY, version="1.4")


def test_to_json_1_3_emits_canonical_casing():
    ca = parse(VALID_1_3_COST_ONLY, version="1.3")
    out = to_json(ca, version="1.3")
    assert '"ContractId":"CONTRACT-1"' in out
    assert '"ContractCommitmentId":"sp-1"' in out
    assert "ContractID" not in out and "ContractCommitmentID" not in out


def test_migrate_normalizes_legacy_casing_and_reports_it():
    sink: set[str] = set()
    out = migrate_1_3_to_1_4(VALID_1_3_COST_ONLY, legacy_sink=sink)
    assert '"ContractId":"CONTRACT-1"' in out
    assert sink == {"ContractID", "ContractCommitmentID"}
    # Canonical input migrates identically, with nothing to report.
    sink.clear()
    assert migrate_1_3_to_1_4(VALID_1_3_CANONICAL, legacy_sink=sink) == out
    assert sink == set()
