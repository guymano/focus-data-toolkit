#!/usr/bin/env python3
"""Capture or verify complete official reports for corrected synthetic datasets.

Default reruns and compares with reviewed evidence. --check-existing verifies archived
reports offline. --record DIR creates a candidate directory, never promotes it.
Reference: focus-validator 2.2.1, models 1.2.0.1/1.3.0.1, applicability ALL.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import urllib.request
from importlib import metadata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from focus_data_toolkit.generators import PROVIDERS, get_generator  # noqa: E402
from focus_data_toolkit.official_report import model_inventory, parse_report  # noqa: E402
from scripts.sample_audit import audit, statistics  # noqa: E402

PINNED_VALIDATOR_VERSION = "2.2.1"
RULE_MODEL_VERSIONS = {"1.2": "1.2.0.1", "1.3": "1.3.0.1"}
MODEL_1_3_URL = (
    "https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec/"
    "releases/download/v1.3/model-1.3.0.1.json"
)
MODEL_1_3_SHA256 = "2c0e38136d12fc5f965c09ef7e005f8ec00dbe2fe6907e8b98d79d8257a0d272"
ROWS = 1000

# ---------------------------------------------------------------------------- #
# Known validator artifacts, verified against focus-validator 2.2.1 with
# --applicability-criteria ALL. Reasons by rule family; composite parents fail
# whenever any child does (cascading up to the dataset-level CostAndUsage-D rules).
# ---------------------------------------------------------------------------- #
_NUMERIC_ID_NOTE = (
    "the CSV loader types AWS's all-digit account ids as BIGINT; the CSV value is a "
    "string (loader type inference, not a data error)"
)
_STATUS_BRANCH_NOTE = (
    "contradictory CommitmentDiscountStatus nullability branches: the status is set "
    "exactly on commitment Usage rows and null elsewhere (pinned by "
    "test_commitment_discount_status_only_on_commitment_usage), but the engine fails "
    "the MUST-be-null and MUST-NOT-be-null branches against each other"
)
_CAPACITY_NOTE = (
    "condition mis-evaluation: CapacityReservationId is null on every row, so the "
    "MUST-NOT-be-null branch for CapacityReservationStatus does not apply"
)
_O39_NOTE = (
    'the ChargeCategory="Purchase" condition of O-039-C is not applied by the '
    "engine, so Used/negotiated usage rows (whose ContractCommitmentId correctly "
    "differs from ResourceId) are flagged; commitment Purchase rows do satisfy "
    "ContractCommitmentId == ResourceId (pinned by "
    "test_contract_applied_on_commitment_purchase_rows)"
)
_CA_BRANCH_NOTE = (
    "contradictory nullability branches for the ContractApplied metric keys: every "
    "element carries exactly one branch — spend commitments a cost, usage "
    "commitments a quantity+unit, never neither (pinned by the ContractApplied "
    "conformance tests) — and the engine fails the cost and quantity branch rules "
    "against each other with mutual upstream-dependency errors"
)
_PCCUP_NOTE = (
    "the SkuPriceId condition is applied inverted: Tax rows (SkuPriceId null, "
    "pricing-currency unit price correctly null per C-011-C) are flagged by the "
    "MUST-NOT-be-null branch"
)
_RESOURCE_TYPE_NOTE = (
    "condition mis-evaluation: rows with a null ResourceId are flagged by the "
    "MUST-NOT-be-null-when-ResourceId-present branch; no generated row has a "
    "ResourceId without a ResourceType (pinned by "
    "test_resource_type_present_wherever_resource_id_is)"
)
_CASCADE_NOTE = "composite parent: fails whenever any child rule fails (cascade only)"
_INVOICE_NOTE = (
    "composite invoice-association branch: every charge in the sample IS "
    "invoice-associated, but the engine picks the not-associated branch "
    "(documented in upstream FOCUS-Sample-Data PR #6)"
)

ARTIFACT_REASONS: dict[str, str] = {
    # 1.2 rule ids (unprefixed)
    "InvoiceId-C-004-C": _INVOICE_NOTE,
    "BillingAccountId-C-000-M": _NUMERIC_ID_NOTE,
    "BillingAccountId-C-002-M": _NUMERIC_ID_NOTE,
    "SubAccountId-C-000-C": _NUMERIC_ID_NOTE,
    "SubAccountId-C-001-M": _NUMERIC_ID_NOTE,
    "CapacityReservationStatus-C-000-C": _CASCADE_NOTE,
    "CapacityReservationStatus-C-002-C": _CASCADE_NOTE,
    "CapacityReservationStatus-C-004-C": _CAPACITY_NOTE,
    "CommitmentDiscountStatus-C-000-C": _CASCADE_NOTE,
    "CommitmentDiscountStatus-C-002-C": _CASCADE_NOTE,
    "CommitmentDiscountStatus-C-003-C": _STATUS_BRANCH_NOTE,
    "CommitmentDiscountStatus-C-004-C": _STATUS_BRANCH_NOTE,
    "CostAndUsage-D-000-M": _CASCADE_NOTE,
    "CostAndUsage-D-002-M": _CASCADE_NOTE,
    # 1.3 rule ids (CAU- prefixed)
    "CAU-BillingAccountId-C-000-M": _NUMERIC_ID_NOTE,
    "CAU-BillingAccountId-C-002-M": _NUMERIC_ID_NOTE,
    "CAU-SubAccountId-C-000-C": _NUMERIC_ID_NOTE,
    "CAU-SubAccountId-C-001-M": _NUMERIC_ID_NOTE,
    "CAU-CapacityReservationStatus-C-000-C": _CASCADE_NOTE,
    "CAU-CapacityReservationStatus-C-002-C": _CASCADE_NOTE,
    "CAU-CapacityReservationStatus-C-004-C": _CAPACITY_NOTE,
    "CAU-CommitmentDiscountStatus-C-000-C": _CASCADE_NOTE,
    "CAU-CommitmentDiscountStatus-C-002-C": _CASCADE_NOTE,
    "CAU-CommitmentDiscountStatus-C-003-C": _STATUS_BRANCH_NOTE,
    "CAU-CommitmentDiscountStatus-C-004-C": _STATUS_BRANCH_NOTE,
    "CAU-ContractApplied-C-000-C": _CASCADE_NOTE,
    "CAU-ContractApplied-C-003-C": _CASCADE_NOTE,
    "CAU-ContractAppliedObject-O-000-C": _CASCADE_NOTE,
    "CAU-ContractAppliedObject-O-028-M": _CASCADE_NOTE,
    "CAU-ContractAppliedObject-O-035-C": _CASCADE_NOTE,
    "CAU-ContractAppliedObject-O-039-C": _O39_NOTE,
    "CAU-ContractAppliedObject-O-041-M": _CASCADE_NOTE,
    "CAU-ContractAppliedObject-O-043-M": _CA_BRANCH_NOTE,
    "CAU-ContractAppliedObject-O-045-M": _CASCADE_NOTE,
    "CAU-ContractAppliedObject-O-046-C": _CA_BRANCH_NOTE,
    "CAU-ContractAppliedObject-O-048-M": _CA_BRANCH_NOTE,
    "CAU-ContractAppliedObject-O-050-M": _CASCADE_NOTE,
    "CAU-ContractAppliedObject-O-052-M": _CA_BRANCH_NOTE,
    "CAU-ContractAppliedObject-O-054-M": _CASCADE_NOTE,
    "CAU-ContractAppliedObject-O-055-C": _CA_BRANCH_NOTE,
    "CAU-ContractAppliedObject-O-057-M": _CA_BRANCH_NOTE,
    "CAU-ContractAppliedObject-O-059-M": _CASCADE_NOTE,
    "CAU-ContractAppliedObject-O-061-M": _CA_BRANCH_NOTE,
    "CAU-ContractAppliedObject-O-064-C": _CASCADE_NOTE,
    "CAU-ContractAppliedObject-O-065-C": _CA_BRANCH_NOTE,
    "CAU-ContractAppliedObject-O-066-C": _CA_BRANCH_NOTE,
    "CAU-CostAndUsage-D-000-M": _CASCADE_NOTE,
    "CAU-CostAndUsage-D-002-M": _CASCADE_NOTE,
    "CAU-PricingCurrencyContractedUnitPrice-C-000-C": _CASCADE_NOTE,
    "CAU-PricingCurrencyContractedUnitPrice-C-003-M": _CASCADE_NOTE,
    "CAU-PricingCurrencyContractedUnitPrice-C-012-C": _PCCUP_NOTE,
    "CAU-ResourceType-C-000-C": _CASCADE_NOTE,
    "CAU-ResourceType-C-003-C": _CASCADE_NOTE,
    "CAU-ResourceType-C-005-C": _RESOURCE_TYPE_NOTE,
}

EVIDENCE = REPO / "tests/fixtures/official/generator_validation"
MODEL_HASHES = {
    "1.2.0.1": "639b302ace9edd05922e3d15fcedf62723c92e7cf25e0a7a6684dd4fd4076fec",
    "1.3.0.1": MODEL_1_3_SHA256,
}
CURRENCY_HASH = "60e9b405692a977040f4232e6f449fd75c251f8608e7c624cb21472d5c151e14"
for _prefix in ("", "CAU-"):
    ARTIFACT_REASONS[_prefix + "EffectiveCost-C-000-M"] = _CASCADE_NOTE
    ARTIFACT_REASONS[_prefix + "EffectiveCost-C-005-C"] = (
        "The model zeroes every Purchase without the future-eligible-charges condition. "
        "Period subscriptions are consumed now; their effective cost equals billed cost. FOCUS_Spec#2673."
    )
ARTIFACT_REASONS["CAU-ContractAppliedObject-O-007-M"] = (
    "The mandatory-five-properties rule contradicts optionalProperties. Generated Spend "
    "elements omit quantity/unit and Usage elements omit cost; FOCUS_Spec#2674 remains unresolved."
)
for _rule in ("042-C", "051-C", "060-C"):
    ARTIFACT_REASONS["CAU-ContractAppliedObject-O-" + _rule] = _CA_BRANCH_NOTE


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_manifest() -> dict[str, str]:
    paths = list((REPO / "src/focus_data_toolkit/generators").rglob("*.py"))
    paths += [REPO / "src/focus_data_toolkit" / name for name in (
        "focus_json.py", "model/focus_json_keys.py", "official_report.py", "_version.py",
    )]
    paths += [Path(__file__), REPO / "scripts/sample_audit.py"]
    return {p.relative_to(REPO).as_posix(): digest(p.read_bytes().replace(b"\r\n", b"\n"))
            for p in sorted(paths)}


def ensure_resources() -> Path:
    if metadata.version("focus-validator") != PINNED_VALIDATOR_VERSION:
        raise ValueError("install focus-validator==2.2.1 for the reference gate")
    import focus_validator
    pkg = Path(focus_validator.__file__).parent
    for version, sha in MODEL_HASHES.items():
        path = pkg / "rules" / f"model-{version}.json"
        if not path.exists():
            url = f"https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec/releases/download/v{version[:3]}/model-{version}.json"
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read()
            if digest(data) != sha:
                raise ValueError(f"downloaded model {version} hash mismatch")
            path.write_bytes(data)
        if digest(path.read_bytes()) != sha:
            raise ValueError(f"installed model {version} hash mismatch")
    if digest((pkg / "rules/currency_codes.csv").read_bytes()) != CURRENCY_HASH:
        raise ValueError("currency resource hash mismatch")
    return pkg.parent


def generated_files():
    for provider in PROVIDERS:
        for version in ("1.2", "1.3"):
            module = get_generator(provider, version)
            data = module.generate_csv_bytes(ROWS)
            rows = list(csv.DictReader(io.StringIO(data.decode())))
            cc_data = module.generate_contract_commitment_csv_bytes(ROWS) if version == "1.3" else None
            cc = list(csv.DictReader(io.StringIO(cc_data.decode()))) if cc_data else None
            audit(rows, provider, version, cc)
            yield f"{provider}_{version}_CostAndUsage", version, "CostAndUsage", data, statistics(rows, provider)
            if cc_data:
                yield f"{provider}_{version}_ContractCommitment", version, "ContractCommitment", cc_data, {"rows": len(cc or [])}


def compare(actual: dict, expected: dict) -> None:
    if actual != expected:
        raise ValueError("rule inventory/state/violation count or provenance differs from reviewed evidence")


def run_report(path: Path, version: str, dataset: str, cwd: Path) -> tuple[bytes, dict]:
    cmd = [sys.executable, "-m", "focus_validator.main", "--data-file", str(path.resolve()),
           "--validate-version", RULE_MODEL_VERSIONS[version], "--focus-dataset", dataset,
           "--applicability-criteria", "ALL", "--show-violations", "--block-download"]
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, env=env)
    path.with_suffix(".log").write_bytes(proc.stdout)
    path.with_suffix(".stderr.log").write_bytes(proc.stderr)
    if proc.returncode or b"Traceback (most recent call last)" in proc.stderr:
        raise ValueError(f"official validator exited {proc.returncode}: {proc.stderr.decode('utf-8', errors='replace')[-2000:]}")
    model_path = cwd / "focus_validator/rules" / f"model-{RULE_MODEL_VERSIONS[version]}.json"
    model = json.loads(model_path.read_text(encoding="utf-8"))
    return proc.stdout, parse_report(proc.stdout.decode("utf-8"), expected_rules=model_inventory(model, dataset))


def failure_evidence(report, model, rows):
    """Every residual rule must be explained; save a minimal source record and rule."""
    proofs = {}
    for key, result in report["rules"].items():
        if result["status"] != "FAIL":
            continue
        if key not in ARTIFACT_REASONS:
            raise ValueError(f"unexplained official failure: {key}")
        rule = model["ModelRules"][key]
        family = key.removeprefix("CAU-")
        candidates = list(enumerate(rows, 1))
        if family.startswith("EffectiveCost-"):
            candidates = [(i, r) for i, r in candidates if r["ChargeCategory"] == "Purchase" and not r["CommitmentDiscountId"]]
        elif family.startswith("ContractApplied"):
            candidates = [(i, r) for i, r in candidates if r.get("ContractApplied")]
            if any(part in family for part in ("O-042-", "O-043-", "O-065-")):
                candidates = [(i, r) for i, r in candidates if "ContractCommitmentAppliedQuantity" in r["ContractApplied"]]
            elif any(part in family for part in ("O-051-", "O-052-", "O-060-", "O-061-")):
                candidates = [(i, r) for i, r in candidates if "ContractCommitmentAppliedQuantity" not in r["ContractApplied"]]
            elif "O-039-" in family:
                candidates = [(i, r) for i, r in candidates if any(e["ContractCommitmentId"] != r["ResourceId"] for e in json.loads(r["ContractApplied"])["Elements"])]
        elif family.startswith("CommitmentDiscountStatus"):
            candidates = [(i, r) for i, r in candidates if not r["CommitmentDiscountId"]]
        elif family.startswith(("ResourceType", "PricingCurrencyContractedUnitPrice")):
            candidates = [(i, r) for i, r in candidates if not r["ResourceId"]]
        assert candidates, key
        i, example = candidates[0]
        # Composite violations count failed expressions, not bad data rows.
        composite = rule["Function"] == "Composite" or family in ("InvoiceId-C-004-C", "ContractAppliedObject-O-007-M")
        if not composite and len(candidates) != result["violations"]:
            raise ValueError(f"{key}: independent population {len(candidates)} != reported {result['violations']}")
        proofs[key] = {"explanation": ARTIFACT_REASONS[key], "model_rule": rule,
                       "kind": "composite" if composite else "row_population",
                       "independent_population": len(candidates), "source_record": i, "example": example}
    return proofs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--record", type=Path, help="new candidate directory (must not exist)")
    modes.add_argument("--check-existing", action="store_true", help="verify archived evidence offline")
    args = parser.parse_args(argv)
    baseline = None if args.record else json.loads((EVIDENCE / "baseline.json").read_text(encoding="utf-8"))
    sources = source_manifest()
    files = list(generated_files())
    resource_pin = {"validator": PINNED_VALIDATOR_VERSION, "models": MODEL_HASHES,
                    "currency_sha256": CURRENCY_HASH, "applicability": "ALL"}
    parameters = {"rows": ROWS, "seeds": {"1.2": 1202, "1.3": 1302}, "include_credits": False}
    if baseline:
        compare(sources, baseline["sources"])
        compare(resource_pin, baseline["resources"])
        compare(parameters, baseline["parameters"])
        if set(baseline["runs"]) != {name for name, *_ in files}:
            raise ValueError("missing or extra dataset run")
    if args.check_existing:
        for name, version, dataset, data, stats in files:
            expected = baseline["runs"][name]
            compare({"data_sha256": digest(data), "statistics": stats},
                    {k: expected[k] for k in ("data_sha256", "statistics")})
            raw = (EVIDENCE / f"{name}.log").read_bytes()
            if digest(raw) != expected["report_sha256"]:
                raise ValueError(f"{name}: archived report hash mismatch")
            compare(parse_report(raw.decode("utf-8")), expected["report"])
            model = {"ModelRules": {k: v["model_rule"] for k, v in expected["failure_evidence"].items()}}
            compare(failure_evidence(expected["report"], model, list(csv.DictReader(io.StringIO(data.decode())))), expected["failure_evidence"])
        print("Verified 9 archived reports, complete rules and provenance")
        return 0
    cwd = ensure_resources()
    if args.record:
        destination = args.record.resolve()
        if destination == EVIDENCE.resolve() or EVIDENCE.resolve() in destination.parents:
            raise ValueError("record candidates outside committed evidence")
        destination.mkdir(parents=True, exist_ok=False)
    else:
        import tempfile
        destination = Path(tempfile.mkdtemp(prefix="focus-official-"))
    result = {"sources": sources, "resources": resource_pin, "parameters": parameters, "runs": {}}
    for name, version, dataset, data, stats in files:
        path = destination / f"{name}.csv"
        path.write_bytes(data)
        raw, report = run_report(path, version, dataset, cwd)
        (destination / f"{name}.log").write_bytes(raw)
        model = json.loads((cwd / "focus_validator/rules" / f"model-{RULE_MODEL_VERSIONS[version]}.json").read_text(encoding="utf-8"))
        proofs = failure_evidence(report, model, list(csv.DictReader(io.StringIO(data.decode()))))
        entry = {"data_sha256": digest(data), "report_sha256": digest(raw), "statistics": stats, "report": report, "failure_evidence": proofs}
        result["runs"][name] = entry
        if baseline:
            # Raw logs can contain machine-specific paths/timing; semantic inventory is exact.
            compare({k: entry[k] for k in ("data_sha256", "statistics", "report", "failure_evidence")},
                    {k: baseline["runs"][name][k] for k in ("data_sha256", "statistics", "report", "failure_evidence")})
        print(f"{name}: PASS={report['passed']} FAIL={report['failed']} SKIPPED={report['skipped']}", flush=True)
    (destination / "baseline.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Complete reports: {destination}. Known FAIL/SKIPPED are not conformance passes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
