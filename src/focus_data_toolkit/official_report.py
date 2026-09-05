"""Strict console-report reader for the official validator (reference: 2.2.1).

Report completeness and execution success are separate from conformance. Never infer
success from the absence of FAIL lines, and never treat SKIPPED as PASS.
"""

from __future__ import annotations

import re
from collections import Counter

_SUMMARY = re.compile(r"Total: (\d+) \| Pass: (\d+) \| Fail: (\d+) \| Skipped: (\d+)")
_ENTRY = re.compile(
    r"^\S+ ([\w.-]+): (PASS|FAIL|SKIPPED) +\(violations=(\d+)(?:,[^\r\n]*)?\)", re.MULTILINE
)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def parse_report(text: str, *, expected_rules: set[str] | None = None) -> dict:
    """Require exactly one complete, internally consistent inventory of rule results."""
    text = _ANSI.sub("", text)
    summaries = _SUMMARY.findall(text)
    if len(summaries) != 1 or "Traceback (most recent call last)" in text:
        raise ValueError("official validator did not produce one complete report")
    total, passed, failed, skipped = map(int, summaries[0])
    entries = _ENTRY.findall(text)
    rules = {key: {"status": status, "violations": int(count)}
             for key, status, count in entries}
    counts = Counter(status for _, status, _ in entries)
    if (not total or len(rules) != len(entries) or total != len(entries)
            or total != passed + failed + skipped
            or [counts[s] for s in ("PASS", "FAIL", "SKIPPED")] != [passed, failed, skipped]):
        raise ValueError("missing/duplicate rule or inconsistent official report totals")
    if any(int(n) for _, state, n in entries if state != "FAIL"):
        raise ValueError("non-failing rule reports violations")
    if expected_rules is not None and set(rules) != expected_rules:
        raise ValueError("official rule inventory differs from the expected model inventory")
    return {"total": total, "passed": passed, "failed": failed, "skipped": skipped,
            "rules": rules}


def model_inventory(model: dict, dataset: str) -> set[str]:
    """Independent transitive closure of dataset roots, dependencies and rule references."""
    pending = list(model["ModelDatasets"][dataset]["ModelRules"])
    seen: set[str] = set()

    def references(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "ModelRuleId":
                    yield item
                else:
                    yield from references(item)
        elif isinstance(value, list):
            for item in value:
                yield from references(item)

    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        rule = model["ModelRules"][key]
        pending.extend(rule["ValidationCriteria"].get("Dependencies", []))
        pending.extend(references(rule))
    return seen
