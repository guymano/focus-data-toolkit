"""Structured, client-actionable diagnostics.

A :class:`Diagnostic` carries everything a user needs to locate and fix a problem in real
client data: the stable rule code, severity, a human message, the business key of the
offending record(s), and — where known — the file, dataset, physical line, column, value,
expected/actual, a fix suggestion, provenance, and the group/join context. It renders to
both JSON (for machine consumption) and a readable console block, and never degrades to
``"invalid input"``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    ERROR = "ERROR"            # a real conformance/integrity violation
    WARNING = "WARNING"        # suspicious, not necessarily invalid
    INFO = "INFO"              # informational
    NOT_EXECUTABLE = "NOT_EXECUTABLE"  # the check could not run (required data absent)
    NOT_APPLICABLE = "NOT_APPLICABLE"  # the check does not apply to this bundle


# Severities that represent an actual failure (used to compute a report's ``ok``).
FAILING_SEVERITIES: frozenset[Severity] = frozenset({Severity.ERROR})


@dataclass(frozen=True)
class Diagnostic:
    """One finding, addressable to a specific record / column / rule."""

    code: str
    severity: Severity
    message: str
    datasets: tuple[str, ...] = ()
    file: str | None = None
    dataset: str | None = None
    line_number: int | None = None
    record_keys: dict[str, str] = field(default_factory=dict)
    column: str | None = None
    value: str | None = None
    expected: str | None = None
    actual: str | None = None
    rule: str | None = None
    suggestion: str | None = None
    provenance: str | None = None
    source: str | None = None
    context: dict[str, str] = field(default_factory=dict)

    @property
    def is_failure(self) -> bool:
        return self.severity in FAILING_SEVERITIES

    def as_dict(self) -> dict:
        """JSON-serialisable view (omitting empty fields)."""
        out: dict = {
            "rule_id": self.code,
            "severity": self.severity.value,
            "message": self.message,
        }
        if self.datasets:
            out["datasets"] = list(self.datasets)
        if self.dataset:
            out["dataset"] = self.dataset
        if self.file:
            out["file"] = self.file
        if self.line_number is not None:
            out["line_number"] = self.line_number
        if self.record_keys:
            out["record_keys"] = dict(self.record_keys)
        if self.column:
            out["column"] = self.column
        if self.value is not None:
            out["value"] = self.value
        if self.expected is not None:
            out["expected"] = self.expected
        if self.actual is not None:
            out["actual"] = self.actual
        if self.rule:
            out["rule"] = self.rule
        if self.suggestion:
            out["suggestion"] = self.suggestion
        if self.provenance:
            out["provenance"] = self.provenance
        if self.source:
            out["source"] = self.source
        if self.context:
            out["context"] = dict(self.context)
        return out

    def as_row(self) -> dict[str, str]:
        """Flat string row for CSV export of large violation sets."""
        return {
            "rule_id": self.code,
            "severity": self.severity.value,
            "datasets": ",".join(self.datasets),
            "dataset": self.dataset or "",
            "file": self.file or "",
            "line_number": "" if self.line_number is None else str(self.line_number),
            "record_keys": ";".join(f"{k}={v}" for k, v in self.record_keys.items()),
            "column": self.column or "",
            "value": "" if self.value is None else self.value,
            "expected": "" if self.expected is None else self.expected,
            "actual": "" if self.actual is None else self.actual,
            "message": self.message,
        }

    def format(self) -> str:
        """Readable multi-line console rendering (see module docstring example)."""
        lines = [f"{self.severity.value} {self.code}: {self.message}"]
        loc = []
        if self.file:
            loc.append(f"file={self.file}")
        if self.dataset:
            loc.append(f"dataset={self.dataset}")
        if self.line_number is not None:
            loc.append(f"row {self.line_number}")
        if self.column:
            loc.append(f"column={self.column}")
        if loc:
            lines.append("  " + " ".join(loc))
        for key, val in self.record_keys.items():
            lines.append(f'  {key}="{val}"')
        if self.expected is not None or self.actual is not None:
            lines.append(f"  expected={self.expected!r} actual={self.actual!r}")
        if self.suggestion:
            lines.append(f"  suggestion: {self.suggestion}")
        return "\n".join(lines)


CSV_FIELDNAMES: tuple[str, ...] = (
    "rule_id",
    "severity",
    "datasets",
    "dataset",
    "file",
    "line_number",
    "record_keys",
    "column",
    "value",
    "expected",
    "actual",
    "message",
)
