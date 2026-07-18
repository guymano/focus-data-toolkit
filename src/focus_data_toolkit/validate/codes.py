"""Stable catalogue of focus-data-toolkit diagnostic codes.

Codes are **stable** identifiers — once assigned they are never renumbered or reused — so
downstream tooling can key on them across releases. Namespaces:

* ``FDT-DET-*``   — schema / version detection
* ``FDT-CROSS-*`` — inter-dataset referential integrity & reconciliation
* ``FDT-ALLOC-*`` — split cost allocation
* ``FDT-CORR-*``  — corrections / credits / billing lifecycle
* ``FDT-IO-*``    — input / output / format

Each code has a default severity and a one-line summary; call :func:`spec` to look one up.
"""

from __future__ import annotations

from dataclasses import dataclass

from focus_data_toolkit.errors import Severity


@dataclass(frozen=True)
class CodeSpec:
    code: str
    default_severity: Severity
    summary: str


def _s(code: str, sev: Severity, summary: str) -> tuple[str, CodeSpec]:
    return code, CodeSpec(code, sev, summary)


CATALOG: dict[str, CodeSpec] = dict([
    # --- detection ---------------------------------------------------------------
    _s("FDT-DET-001", Severity.ERROR, "schema/version detection confidence too low"),
    _s("FDT-DET-002", Severity.ERROR, "ambiguous schema (multiple candidate versions/datasets)"),
    _s("FDT-DET-003", Severity.ERROR, "forced version incompatible with the header"),
    _s("FDT-DET-004", Severity.WARNING, "unknown non-x_ columns present in the source"),
    # --- multi-provider / context ------------------------------------------------
    _s("FDT-CTX-001", Severity.WARNING, "source carries multiple provider contexts"),
    _s("FDT-CTX-002", Severity.WARNING, "source carries multiple invoice issuers"),
    _s("FDT-CTX-003", Severity.WARNING, "source carries multiple billing currencies"),
    _s("FDT-CTX-004", Severity.INFO, "a representative context was chosen for enrichment"),
    # --- cross-dataset referential integrity & reconciliation --------------------
    _s("FDT-CROSS-001", Severity.ERROR, "duplicate identifier where uniqueness is required"),
    _s("FDT-CROSS-002", Severity.ERROR, "identifier collides across datasets"),
    _s("FDT-CROSS-010", Severity.ERROR, "ContractApplied references an unknown ContractCommitmentId"),
    _s("FDT-CROSS-011", Severity.ERROR, "referenced Contract Commitment not found"),
    _s("FDT-CROSS-014", Severity.ERROR, "Cost and Usage InvoiceDetailId not found in Invoice Detail"),
    _s("FDT-CROSS-015", Severity.ERROR, "linked record differs on an identifying attribute (wrong invoice line)"),
    _s("FDT-CROSS-020", Severity.ERROR, "currency mismatch between linked records"),
    _s("FDT-CROSS-021", Severity.ERROR, "billing period mismatch between linked records"),
    _s("FDT-CROSS-022", Severity.ERROR, "invoice issuer mismatch between linked records"),
    _s("FDT-CROSS-023", Severity.ERROR, "billing account mismatch between linked records"),
    _s("FDT-CROSS-030", Severity.ERROR, "sum reconciliation mismatch beyond tolerance"),
    _s("FDT-CROSS-031", Severity.WARNING, "invoice-detail line has no matching Cost and Usage rows"),
    _s("FDT-CROSS-040", Severity.ERROR, "no Billing Period for a (period, issuer) seen in Cost and Usage"),
    _s("FDT-CROSS-041", Severity.ERROR, "a closed billing period was modified"),
    _s("FDT-CROSS-050", Severity.ERROR, "contract commitment period start is not before its end"),
    _s("FDT-CROSS-051", Severity.ERROR, "percentage value outside its allowed range"),
    # --- split cost allocation ---------------------------------------------------
    _s("FDT-ALLOC-001", Severity.ERROR, "allocation ratios do not sum to 1 within tolerance"),
    _s("FDT-ALLOC-002", Severity.ERROR, "allocated costs do not sum to the origin charge"),
    _s("FDT-ALLOC-003", Severity.ERROR, "inconsistent allocation method within a group"),
    _s("FDT-ALLOC-004", Severity.ERROR, "duplicate allocated resource within a group"),
    _s("FDT-ALLOC-005", Severity.ERROR, "incomplete allocation group (missing required information)"),
    _s("FDT-ALLOC-006", Severity.ERROR, "allocation ratio outside [0, 1]"),
    _s("FDT-ALLOC-007", Severity.ERROR, "inconsistent unit within an allocation group"),
    # --- corrections / lifecycle -------------------------------------------------
    _s("FDT-CORR-001", Severity.ERROR, "correction references a missing invoice/charge"),
    _s("FDT-CORR-002", Severity.ERROR, "net sum of a correction set does not reconcile"),
    _s("FDT-CORR-003", Severity.ERROR, "correction overwrites an original row (no audit trail)"),
    _s("FDT-CORR-004", Severity.ERROR, "invoice status transition is not allowed"),
    # --- bundle coverage (a check could not run / does not apply) ----------------
    _s("FDT-BUNDLE-001", Severity.NOT_EXECUTABLE, "a cross-dataset check could not run (data absent)"),
    _s("FDT-BUNDLE-002", Severity.NOT_APPLICABLE, "a cross-dataset check does not apply to this bundle"),
    # --- io / format -------------------------------------------------------------
    _s("FDT-IO-001", Severity.ERROR, "malformed input record (wrong field count)"),
    _s("FDT-IO-002", Severity.ERROR, "decimal value exceeds the target Parquet scale/precision"),
    _s("FDT-IO-003", Severity.ERROR, "destination already exists"),
    _s("FDT-IO-004", Severity.WARNING, "high-cardinality Parquet partition key (many small files)"),
    _s("FDT-IO-005", Severity.ERROR, "insufficient free space on the output filesystem"),
    _s("FDT-IO-006", Severity.ERROR, "insufficient work-filesystem space or temp budget exceeded"),
])


def spec(code: str) -> CodeSpec:
    """Return the :class:`CodeSpec` for ``code`` (raises ``KeyError`` if unknown)."""
    return CATALOG[code]


def default_severity(code: str) -> Severity:
    return CATALOG[code].default_severity
