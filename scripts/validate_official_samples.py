#!/usr/bin/env python3
"""Gate the generated sample datasets through the official FinOps FOCUS validator.

Generates the nine outputs — Cost and Usage for every provider at FOCUS 1.2 and 1.3,
plus the 1.3 Contract Commitment dataset — and runs ``focus-validator`` (pinned
``2.2.1``) against the matching rule model (``1.2.0.1`` / ``1.3.0.1``) with
``--applicability-criteria ALL``, so the conditional rules for the capabilities these
datasets deliberately exercise (commitment discounts, contract commitments,
negotiated pricing, multi-currency, split allocation, …) run too, not only the
universal ones.

Each ``(version, provider, dataset)`` run is compared against its **exact** expected
artifact set: an artifact tolerated for one run (e.g. the CSV loader typing AWS's
all-digit account ids as numbers) is never silently tolerated for another. Any
non-expected rule failure fails the gate — and so does an expected artifact that
*stops* failing, so stale allowlist entries are pruned instead of rotting.

Known artifacts are validator-side, not data-side: every entry names why the data is
nevertheless conformant, and each claim is pinned by a data-side assertion in
``tests/test_generated_conformance.py``. They are printed on every run so they stay
visible.

The 1.2.0.1 rule model ships inside the focus-validator package; the 1.3.0.1 model is
fetched once from the FOCUS_Spec GitHub release into the package's ``rules/``
directory — **SHA-256 pinned** — after which validation runs fully offline
(``--block-download``, also avoiding GitHub API rate limits in CI). The validator
resolves its bundled ``currency_codes.csv`` relative to the working directory, so it
is invoked from the site-packages root.

Usage:  python scripts/validate_official_samples.py  (requires Python >= 3.12 and
        pip install focus-validator==2.2.1)
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import tempfile
import urllib.request
from importlib import metadata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from focus_data_toolkit.generators import PROVIDERS, get_generator  # noqa: E402

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

# Exact expected artifact set per (version, provider, dataset). Verified per run —
# an AWS-only loader artifact appearing on Azure/GCP is an unexpected failure, and
# an expected artifact that stops failing is a stale entry (both fail the gate).
_COMMON_1_2 = frozenset({
    "InvoiceId-C-004-C",
    "CapacityReservationStatus-C-000-C",
    "CapacityReservationStatus-C-002-C",
    "CapacityReservationStatus-C-004-C",
    "CommitmentDiscountStatus-C-000-C",
    "CommitmentDiscountStatus-C-002-C",
    "CommitmentDiscountStatus-C-004-C",
    "CostAndUsage-D-000-M",
    "CostAndUsage-D-002-M",
})
_AWS_NUMERIC_1_2 = frozenset({
    "BillingAccountId-C-000-M",
    "BillingAccountId-C-002-M",
    "SubAccountId-C-000-C",
    "SubAccountId-C-001-M",
})
_COMMON_1_3 = frozenset({
    "CAU-CapacityReservationStatus-C-000-C",
    "CAU-CapacityReservationStatus-C-002-C",
    "CAU-CapacityReservationStatus-C-004-C",
    "CAU-CommitmentDiscountStatus-C-000-C",
    "CAU-CommitmentDiscountStatus-C-002-C",
    "CAU-CommitmentDiscountStatus-C-003-C",
    "CAU-CommitmentDiscountStatus-C-004-C",
    "CAU-ContractApplied-C-000-C",
    "CAU-ContractApplied-C-003-C",
    "CAU-ContractAppliedObject-O-000-C",
    "CAU-ContractAppliedObject-O-028-M",
    "CAU-ContractAppliedObject-O-035-C",
    "CAU-ContractAppliedObject-O-039-C",
    "CAU-ContractAppliedObject-O-041-M",
    "CAU-ContractAppliedObject-O-043-M",
    "CAU-ContractAppliedObject-O-045-M",
    "CAU-ContractAppliedObject-O-046-C",
    "CAU-ContractAppliedObject-O-048-M",
    "CAU-ContractAppliedObject-O-050-M",
    "CAU-ContractAppliedObject-O-052-M",
    "CAU-ContractAppliedObject-O-054-M",
    "CAU-ContractAppliedObject-O-055-C",
    "CAU-ContractAppliedObject-O-057-M",
    "CAU-ContractAppliedObject-O-059-M",
    "CAU-ContractAppliedObject-O-061-M",
    "CAU-ContractAppliedObject-O-064-C",
    "CAU-ContractAppliedObject-O-065-C",
    "CAU-ContractAppliedObject-O-066-C",
    "CAU-CostAndUsage-D-000-M",
    "CAU-CostAndUsage-D-002-M",
    "CAU-PricingCurrencyContractedUnitPrice-C-000-C",
    "CAU-PricingCurrencyContractedUnitPrice-C-003-M",
    "CAU-PricingCurrencyContractedUnitPrice-C-012-C",
    "CAU-ResourceType-C-000-C",
    "CAU-ResourceType-C-003-C",
    "CAU-ResourceType-C-005-C",
})
_AWS_NUMERIC_1_3 = frozenset({
    "CAU-BillingAccountId-C-000-M",
    "CAU-BillingAccountId-C-002-M",
    "CAU-SubAccountId-C-000-C",
    "CAU-SubAccountId-C-001-M",
})

EXPECTED_ARTIFACTS: dict[tuple[str, str, str], frozenset[str]] = {
    ("1.2", "aws", "CostAndUsage"): _COMMON_1_2 | _AWS_NUMERIC_1_2,
    ("1.2", "azure", "CostAndUsage"): _COMMON_1_2,
    ("1.2", "gcp", "CostAndUsage"): _COMMON_1_2,
    ("1.3", "aws", "CostAndUsage"): _COMMON_1_3 | _AWS_NUMERIC_1_3,
    ("1.3", "azure", "CostAndUsage"): _COMMON_1_3,
    ("1.3", "gcp", "CostAndUsage"): _COMMON_1_3,
    ("1.3", "aws", "ContractCommitment"): frozenset(),
    ("1.3", "azure", "ContractCommitment"): frozenset(),
    ("1.3", "gcp", "ContractCommitment"): frozenset(),
}

_FAIL_LINE = re.compile(r"^❌\s+(?P<rule>[A-Za-z0-9_.-]+):\s+FAIL", re.MULTILINE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validator_package_dir() -> Path:
    installed = metadata.version("focus-validator")
    if installed != PINNED_VALIDATOR_VERSION:
        raise SystemExit(
            f"focus-validator {installed} installed; the gate is pinned to "
            f"{PINNED_VALIDATOR_VERSION} (the expected-artifact sets are verified "
            "against exactly that version)"
        )
    import focus_validator

    return Path(focus_validator.__file__).parent


def _ensure_rule_model(pkg_dir: Path, model_version: str) -> None:
    target = pkg_dir / "rules" / f"model-{model_version}.json"
    if not target.exists():
        print(f"fetching rule model {model_version} -> {target}")
        with urllib.request.urlopen(MODEL_1_3_URL, timeout=60) as resp:
            target.write_bytes(resp.read())
    digest = _sha256(target)
    if digest != MODEL_1_3_SHA256:
        target.unlink(missing_ok=True)
        raise SystemExit(
            f"rule model {model_version} SHA-256 mismatch: got {digest}, expected "
            f"{MODEL_1_3_SHA256} (source: {MODEL_1_3_URL}); the file was removed — "
            "verify the release asset before re-running"
        )


def _run_validator(
    data_file: Path, model_version: str, *, dataset: str, cwd: Path
) -> set[str]:
    cmd = [
        sys.executable, "-m", "focus_validator.main",
        "--data-file", str(data_file),
        "--validate-version", model_version,
        "--applicability-criteria", "ALL",
        "--block-download",
    ]
    if dataset != "CostAndUsage":
        cmd += ["--focus-dataset", dataset]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    failed = {m.group("rule") for m in _FAIL_LINE.finditer(proc.stdout)}
    if proc.returncode != 0 and not failed:
        print(proc.stdout[-4000:])
        print(proc.stderr[-4000:], file=sys.stderr)
        raise SystemExit(f"focus-validator crashed on {data_file.name}")
    return failed


def main() -> int:
    pkg_dir = _validator_package_dir()
    _ensure_rule_model(pkg_dir, RULE_MODEL_VERSIONS["1.3"])
    site_root = pkg_dir.parent  # the validator reads currency_codes.csv relative to cwd

    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        runs: list[tuple[str, str, str, Path]] = []
        for provider in PROVIDERS:
            for version in ("1.2", "1.3"):
                module = get_generator(provider, version)
                cu = tmpdir / f"{provider}_{version}_cost_and_usage.csv"
                cu.write_bytes(module.generate_csv_bytes(ROWS))
                runs.append((version, provider, "CostAndUsage", cu))
                if version == "1.3":
                    cc = tmpdir / f"{provider}_{version}_contract_commitment.csv"
                    cc.write_bytes(module.generate_contract_commitment_csv_bytes(ROWS))
                    runs.append((version, provider, "ContractCommitment", cc))

        assert len(runs) == 9 and {(v, p, d) for v, p, d, _ in runs} == set(EXPECTED_ARTIFACTS)
        for version, provider, dataset, data_file in runs:
            label = f"{provider} {version} {dataset}"
            expected = EXPECTED_ARTIFACTS[(version, provider, dataset)]
            failed = _run_validator(
                data_file, RULE_MODEL_VERSIONS[version], dataset=dataset, cwd=site_root
            )
            for rule in sorted(failed & expected):
                print(f"  [known artifact] {label}: {rule} — {ARTIFACT_REASONS[rule]}")
            unexpected = sorted(failed - expected)
            stale = sorted(expected - failed)
            for rule in unexpected:
                print(f"  [FAIL] {label}: unexpected failure {rule}")
            for rule in stale:
                print(f"  [FAIL] {label}: stale allowlist entry {rule} no longer fails — prune it")
            problems.extend(f"{label}: unexpected {rule}" for rule in unexpected)
            problems.extend(f"{label}: stale {rule}" for rule in stale)
            if not unexpected and not stale:
                print(f"OK {label} ({len(expected)} known artifact(s), exact match)")

    if problems:
        print(f"\n{len(problems)} gate problem(s):")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("\nno unexpected validator failures across 9 validation runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
