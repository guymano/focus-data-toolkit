"""Official FOCUS JSON object schemas: vendoring provenance + deep validation.

The four official schemas (spec repo ``specification/schemas/datasets/``, tag v1.4)
are vendored verbatim under ``model/json_schemas/`` and enforced by the linter for
``ContractApplied``, ``AllocatedMethodDetails``,
``CommitmentProgramEligibilityDetails`` and ``ContractCommitmentApplicability``.
"""

from __future__ import annotations

import hashlib
import json

from focus_data_toolkit.model.json_schema_check import (
    OFFICIAL_SCHEMA_COLUMNS,
    PROVENANCE_PATH,
    SCHEMA_DIR,
    check_against_official_schema,
    load_official_schema,
)
from focus_data_toolkit.model.validator import lint_focus_1_4_structure


def _rules_for(dataset: str, overrides: dict[str, str], column: str) -> set[str]:
    report = lint_focus_1_4_structure(dataset, [overrides])
    return {v.rule for v in report.violations if v.column == column}


# --------------------------------------------------------------------------- #
# Vendoring provenance
# --------------------------------------------------------------------------- #
def test_provenance_hashes_match_vendored_files():
    manifest = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    assert manifest["source_ref"] == "v1.4"
    assert manifest["license"] == "CC-BY-4.0"
    listed = {entry["file"] for entry in manifest["files"]}
    assert listed == set(OFFICIAL_SCHEMA_COLUMNS.values())
    for entry in manifest["files"]:
        data = (SCHEMA_DIR / entry["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["file"]
        assert len(data) == entry["bytes"], entry["file"]


def test_every_official_schema_column_is_registered():
    manifest = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    assert {e["column"] for e in manifest["files"]} == set(OFFICIAL_SCHEMA_COLUMNS)


def test_all_vendored_schemas_load_within_supported_subset():
    for filename in OFFICIAL_SCHEMA_COLUMNS.values():
        assert load_official_schema(filename)["$schema"].endswith("2020-12/schema")


# --------------------------------------------------------------------------- #
# ContractCommitmentApplicability — conditional scope rules
# --------------------------------------------------------------------------- #
def test_applicability_x_source_only_is_rejected():
    # The pre-fix synthetic shape: no scope flag -> Inclusions/InclusionOperator required.
    errors = check_against_official_schema(
        "ContractCommitmentApplicability", {"x_Source": "synthetic"}
    )
    assert errors


def test_applicability_complex_scope_is_conformant():
    obj = {"IsComplexScope": True, "x_Source": "synthetic"}
    assert check_against_official_schema("ContractCommitmentApplicability", obj) == []


def test_applicability_global_scope_forbids_inclusions():
    obj = {
        "IsGlobalScope": True,
        "InclusionOperator": "And",
        "Inclusions": [{"Dimension": "RegionId", "Operator": "In", "Values": ["eu-west-1"]}],
    }
    assert check_against_official_schema("ContractCommitmentApplicability", obj)


def test_applicability_explicit_inclusions_are_conformant():
    obj = {
        "InclusionOperator": "And",
        "Inclusions": [{"Dimension": "RegionId", "Operator": "In", "Values": ["eu-west-1"]}],
    }
    assert check_against_official_schema("ContractCommitmentApplicability", obj) == []


def test_applicability_both_scopes_rejected():
    obj = {"IsGlobalScope": True, "IsComplexScope": True}
    assert check_against_official_schema("ContractCommitmentApplicability", obj)


def test_applicability_exclusions_require_operator():
    obj = {
        "IsGlobalScope": True,
        "Exclusions": [{"Dimension": "RegionId", "Operator": "In", "Values": ["us-east-1"]}],
    }
    assert check_against_official_schema("ContractCommitmentApplicability", obj)


def test_applicability_exists_operator_requires_wildcard():
    obj = {
        "InclusionOperator": "And",
        "Inclusions": [{"Dimension": "Tags", "Operator": "Exists", "Values": ["prod"]}],
    }
    assert check_against_official_schema("ContractCommitmentApplicability", obj)
    ok = {
        "InclusionOperator": "And",
        "Inclusions": [{"Dimension": "Tags", "Operator": "Exists", "Values": ["*"]}],
    }
    assert check_against_official_schema("ContractCommitmentApplicability", ok) == []


def test_lint_flags_nonconformant_applicability():
    row = {"ContractCommitmentApplicability": json.dumps({"x_Source": "synthetic"})}
    assert "official_schema_violation" in _rules_for(
        "Contract Commitment", row, "ContractCommitmentApplicability"
    )


def test_lint_accepts_conformant_applicability():
    row = {
        "ContractCommitmentApplicability": json.dumps(
            {"IsComplexScope": True, "x_Source": "synthetic"}
        )
    }
    assert "official_schema_violation" not in _rules_for(
        "Contract Commitment", row, "ContractCommitmentApplicability"
    )


# --------------------------------------------------------------------------- #
# AllocatedMethodDetails / CommitmentProgramEligibilityDetails — new depth
# --------------------------------------------------------------------------- #
def test_allocated_method_details_empty_elements_rejected():
    # Passes the shallow registry check but violates the official minItems: 1.
    row = {"AllocatedMethodDetails": json.dumps({"Elements": []})}
    assert "official_schema_violation" in _rules_for(
        "Cost and Usage", row, "AllocatedMethodDetails"
    )


def test_allocated_method_details_ratio_out_of_range_rejected():
    row = {"AllocatedMethodDetails": json.dumps({"Elements": [{"AllocatedRatio": 1.5}]})}
    assert "official_schema_violation" in _rules_for(
        "Cost and Usage", row, "AllocatedMethodDetails"
    )


def test_allocated_method_details_quantity_without_unit_rejected():
    row = {
        "AllocatedMethodDetails": json.dumps(
            {"Elements": [{"AllocatedRatio": 0.5, "UsageQuantity": 2}]}
        )
    }
    assert "official_schema_violation" in _rules_for(
        "Cost and Usage", row, "AllocatedMethodDetails"
    )


def test_allocated_method_details_valid_element_accepted():
    row = {
        "AllocatedMethodDetails": json.dumps(
            {"Elements": [{"AllocatedRatio": 0.5, "UsageUnit": "GB", "UsageQuantity": 12.5}]}
        )
    }
    assert _rules_for("Cost and Usage", row, "AllocatedMethodDetails") == set()


def test_commitment_program_eligibility_missing_program_type_rejected():
    row = {"CommitmentProgramEligibilityDetails": json.dumps({"CommitmentPrograms": [{}]})}
    assert "official_schema_violation" in _rules_for(
        "Cost and Usage", row, "CommitmentProgramEligibilityDetails"
    )


# --------------------------------------------------------------------------- #
# ContractApplied — official oneOf metric exclusivity backs the typed parser
# --------------------------------------------------------------------------- #
def test_contract_applied_official_schema_cost_scenario():
    ok = {
        "Elements": [
            {"ContractId": "c", "ContractCommitmentId": "cc",
             "ContractCommitmentAppliedCost": 12.5}
        ]
    }
    assert check_against_official_schema("ContractApplied", ok) == []
    both = {
        "Elements": [
            {"ContractId": "c", "ContractCommitmentId": "cc",
             "ContractCommitmentAppliedCost": 1,
             "ContractCommitmentAppliedQuantity": 2,
             "ContractCommitmentAppliedUnit": "Hours"}
        ]
    }
    assert check_against_official_schema("ContractApplied", both)
