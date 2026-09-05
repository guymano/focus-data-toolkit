"""False-green, report integrity and evidence provenance regression tests."""
from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from focus_data_toolkit.official_report import model_inventory, parse_report
from focus_data_toolkit.official_validator import run_official_validator
from scripts.validate_official_samples import compare, run_report, source_manifest

GOOD = """Total: 3 | Pass: 1 | Fail: 1 | Skipped: 1
[PASS] A-001: PASS  (violations=0)
[FAIL] B-001: FAIL  (violations=2, msg=condition)
[SKIP] C-001: SKIPPED  (violations=0, reason=dynamic)
"""


@pytest.mark.parametrize("bad", [
    "", GOOD.replace("Total: 3", "Total: 4"), GOOD + GOOD,
    GOOD.replace("B-001", "A-001"), GOOD[:GOOD.index("[SKIP]")],
    GOOD.replace("violations=0", "violations=1", 1),
    GOOD + "Traceback (most recent call last)",
])
def test_refuse_incomplete_or_inconsistent_reports(bad):
    with pytest.raises(ValueError):
        parse_report(bad)


def test_inventory_and_violation_counts_are_not_discarded():
    result = parse_report(GOOD, expected_rules={"A-001", "B-001", "C-001"})
    assert result["rules"]["B-001"] == {"status": "FAIL", "violations": 2}
    with pytest.raises(ValueError, match="inventory"):
        parse_report(GOOD, expected_rules={"A-001"})
    for field, value in (("status", "SKIPPED"), ("violations", 99)):
        changed = copy.deepcopy(result)
        changed["rules"]["A-001"][field] = value
        with pytest.raises(ValueError):
            compare(changed, result)


@pytest.mark.parametrize(("text", "rc", "expected"), [(GOOD, 0, 1), ("", 0, 1), (GOOD, 7, 7),
    ("Total: 1 | Pass: 1 | Fail: 0 | Skipped: 0\n[PASS] A-001: PASS (violations=0)", 0, 0)])
def test_public_validator_reads_verdict(monkeypatch, tmp_path, text, rc, expected):
    monkeypatch.setattr("focus_data_toolkit.official_validator._executable", lambda: "fake-validator")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=text, stderr="", returncode=rc))
    assert run_official_validator(tmp_path / "sample.csv", "1.3.0.1") == expected


def test_crash_is_failure_even_when_report_contains_known_failures(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=GOOD.encode(), stderr=b"crash", returncode=1))
    with pytest.raises(ValueError, match="exited"):
        run_report(tmp_path / "sample.csv", "1.3", "CostAndUsage", tmp_path)
    assert (tmp_path / "sample.log").read_bytes() == GOOD.encode()


def test_all_generation_sources_are_fingerprinted():
    sources = source_manifest()
    assert "src/focus_data_toolkit/generators/engine/scenarios_core.py" in sources
    assert "src/focus_data_toolkit/model/focus_json_keys.py" in sources
    for name in sources:
        modified = dict(sources)
        modified[name] = "0" * 64
        with pytest.raises(ValueError):
            compare(modified, sources)
    with pytest.raises(ValueError):
        compare({**sources, "extra-module.py": "0" * 64}, sources)


def test_inventory_follows_dependencies_and_nested_references():
    model = {"ModelDatasets": {"CostAndUsage": {"ModelRules": ["root"]}},
             "ModelRules": {
                 "root": {"ValidationCriteria": {"Dependencies": ["dependency"]},
                          "children": [{"ModelRuleId": "child"}]},
                 "dependency": {"ValidationCriteria": {}, "ModelRuleId": "root"},
                 "child": {"ValidationCriteria": {}},
                 "unrelated": {"ValidationCriteria": {}},
             }}
    assert model_inventory(model, "CostAndUsage") == {"root", "dependency", "child"}
    del model["ModelRules"]["child"]
    with pytest.raises(KeyError):
        model_inventory(model, "CostAndUsage")


def test_model_resource_and_parameter_changes_are_rejected():
    from scripts.validate_official_samples import CURRENCY_HASH, MODEL_HASHES
    expected = {"models": MODEL_HASHES, "currency": CURRENCY_HASH, "rows": 1000}
    for key in expected:
        with pytest.raises(ValueError):
            compare({**expected, key: "modified"}, expected)


def test_parse_real_archived_console_report():
    path = Path(__file__).parent / "fixtures/official/generator_validation/aws_1.3_ContractCommitment.log"
    report = parse_report(path.read_text(encoding="utf-8"))
    assert {k: report[k] for k in ("total", "passed", "failed", "skipped")} == {
        "total": 114, "passed": 76, "failed": 0, "skipped": 38,
    }
    assert len(report["rules"]) == 114


@pytest.mark.parametrize("args", [
    ("--output-type", "unittest"), ("--output-type=web",), ("--output-type", "json"),
    ("--output-type",), ("--output-destination", "report.txt"),
])
def test_reject_conflicting_output_before_launch(monkeypatch, tmp_path, capsys, args):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("must reject before launch"))
    assert run_official_validator(tmp_path / "sample.csv", "1.3.0.1", extra_args=args) == 2
    assert "requires console output" in capsys.readouterr().err


def test_warn_on_untested_validator_version(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("focus_data_toolkit.official_validator._executable", lambda: "fake")
    monkeypatch.setattr("focus_data_toolkit.official_validator.metadata.version", lambda _: "9.9.9")
    good = "Total: 1 | Pass: 1 | Fail: 0 | Skipped: 0\n[PASS] A-001: PASS (violations=0)"
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=good, stderr="", returncode=0))
    assert run_official_validator(tmp_path / "sample.csv", "1.3.0.1") == 0
    assert "installed version is 9.9.9" in capsys.readouterr().err


def test_importing_audit_scripts_does_not_mutate_sys_path():
    code = ("import sys; before = sys.path.copy(); "
            "import scripts.validate_official_samples, scripts.describe_generated_samples; "
            "assert sys.path == before")
    subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parents[1], check=True)


def test_archived_stderr_is_checked(monkeypatch, tmp_path):
    import json

    import scripts.validate_official_samples as gate

    original = json.loads((gate.EVIDENCE / "baseline.json").read_text(encoding="utf-8"))
    # Keep this test independent of a pending re-record after source changes.
    original["sources"] = gate.source_manifest()
    (tmp_path / "baseline.json").write_text(json.dumps(original), encoding="utf-8")
    name = next(iter(original["runs"]))
    (tmp_path / f"{name}.log").write_bytes((gate.EVIDENCE / f"{name}.log").read_bytes())
    (tmp_path / f"{name}.stderr.log").write_text("unexpected diagnostic", encoding="utf-8")
    monkeypatch.setattr(gate, "EVIDENCE", tmp_path)
    with pytest.raises(ValueError, match="archived stderr"):
        gate.main(["--check-existing"])
