"""Validate a *bundle* of FOCUS datasets together (P1.4).

This layer is deliberately separate from the per-dataset linter (``model/validator.py``): it
asserts the cross-dataset guarantees the linter explicitly does not — referential integrity,
uniqueness, currency/period/issuer coherence, reconciliation, split cost allocation, and
commitment lifecycle. ``validate_dataset_bundle`` returns a :class:`BundleReport` whose
diagnostics are grouped by severity (error / warning / info / not-executable / not-applicable)
and which serialises to JSON.

Memory model: no dataset is ever materialised. Each check consumes its inputs in independent
forward passes, so every bundle value must be **re-iterable** — a list, or an object whose
``__iter__`` opens a fresh scan (e.g. a staged-file reader); a one-shot generator is rejected.
Per-key lookup state (seen ids, foreign-key targets, running sums) is created through
``index_factory``, which a streaming caller points at a disk-spilling map
(:class:`~focus_data_toolkit.storage.spill.SpillableIndexPool`) to validate bundles far
larger than RAM.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from decimal import Decimal

from focus_data_toolkit.errors import Diagnostic, Severity
from focus_data_toolkit.validate import allocation, corrections, reconciliation, referential

Rows = Iterable[Mapping[str, str]]
Bundle = Mapping[str, Rows]
IndexFactory = Callable[[], MutableMapping[str, str]]


@dataclass
class BundleReport:
    """Result of validating a dataset bundle."""

    diagnostics: list[Diagnostic]
    checks_run: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """No failing (ERROR) diagnostics."""
        return not any(d.is_failure for d in self.diagnostics)

    def by_severity(self, severity: Severity) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity is severity]

    @property
    def errors(self) -> list[Diagnostic]:
        return self.by_severity(Severity.ERROR)

    @property
    def warnings(self) -> list[Diagnostic]:
        return self.by_severity(Severity.WARNING)

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {s.value: 0 for s in Severity}
        for diag in self.diagnostics:
            counts[diag.severity.value] += 1
        return counts

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "counts": self.counts(),
            "checks_run": list(self.checks_run),
            "diagnostics": [d.as_dict() for d in self.diagnostics],
        }

    def format(self) -> str:
        counts = self.counts()
        header = "bundle validation: " + ("OK" if self.ok else "FAILED")
        summary = ", ".join(f"{k.lower()}={v}" for k, v in counts.items() if v)
        blocks = [header + (f" ({summary})" if summary else "")]
        blocks.extend(d.format() for d in self.diagnostics)
        return "\n".join(blocks)


def _note(code: str, severity: Severity, message: str, datasets: tuple[str, ...]) -> Diagnostic:
    return Diagnostic(code=code, severity=severity, message=message, datasets=datasets)


def _reiterable(name: str, rows: Rows) -> Rows:
    """Reject one-shot iterators: every check opens its own fresh pass over the rows."""
    if iter(rows) is rows:
        raise TypeError(
            f"bundle dataset {name!r} is a one-shot iterator; validate_dataset_bundle "
            "requires re-iterable rows (a list, or an object whose __iter__ opens a fresh scan)"
        )
    return rows


def _has_rows(rows: Rows) -> bool:
    for _ in rows:
        return True
    return False


def validate_dataset_bundle(
    bundle: Bundle,
    *,
    invoice_detail_authoritative: bool = False,
    rounding_tolerance: Decimal | None = None,
    index_factory: IndexFactory | None = None,
) -> BundleReport:
    """Validate the datasets in ``bundle`` against each other.

    ``bundle`` maps FOCUS dataset names to their rows — each value must be re-iterable (see
    the module docstring); nothing is materialised. ``invoice_detail_authoritative`` gates
    the Cost-and-Usage <-> Invoice-Detail sum reconciliation: it runs only when the Invoice
    Detail comes from a real invoice (a toolkit-derived Invoice Detail reconciles by
    construction, so reconciling it would be circular). ``rounding_tolerance`` overrides the
    reconciliation tolerance. ``index_factory`` supplies the per-key lookup state (``dict``
    when omitted; pass ``SpillableIndexPool(...).make_map`` for bounded memory).
    """
    factory: IndexFactory = index_factory if index_factory is not None else dict
    cu = _reiterable("Cost and Usage", bundle.get("Cost and Usage", ()))
    invd = _reiterable("Invoice Detail", bundle.get("Invoice Detail", ()))
    bp = _reiterable("Billing Period", bundle.get("Billing Period", ()))
    cc = _reiterable("Contract Commitment", bundle.get("Contract Commitment", ()))
    has_cu, has_invd, has_bp, has_cc = (_has_rows(r) for r in (cu, invd, bp, cc))

    diagnostics: list[Diagnostic] = []
    checks: list[str] = []

    def run(name: str, produced: list[Diagnostic]) -> None:
        checks.append(name)
        diagnostics.extend(produced)

    def _refs_present(rows: Rows, column: str) -> bool:
        return any((r.get(column) or "").strip() for r in rows)

    # Referential integrity.
    if has_invd:
        run(
            "unique_invoice_detail_ids",
            referential.check_unique_invoice_detail_ids(invd, index_factory=factory),
        )
    if has_cc:
        run(
            "unique_contract_commitment_ids",
            referential.check_unique_contract_commitment_ids(cc, index_factory=factory),
        )
    if has_cu and has_invd:
        run(
            "cost_and_usage_invoice_detail_fk",
            referential.check_cost_and_usage_invoice_detail_fk(
                cu, invd, index_factory=factory
            ),
        )
        run(
            "cost_and_usage_invoice_detail_consistency",
            referential.check_cost_and_usage_invoice_detail_consistency(
                cu, invd, index_factory=factory
            ),
        )
    elif has_cu and _refs_present(cu, "InvoiceDetailId"):
        # References exist but their target table is absent -> the FK check cannot resolve them.
        diagnostics.append(
            _note(
                "FDT-BUNDLE-001",
                Severity.NOT_EXECUTABLE,
                "Cost and Usage InvoiceDetailId references cannot be checked: the Invoice Detail "
                "dataset is absent from the bundle",
                ("Cost and Usage", "Invoice Detail"),
            )
        )
    if has_cu and has_cc:
        run(
            "contract_applied_fk",
            referential.check_contract_applied_fk(cu, cc, index_factory=factory),
        )
    elif has_cu and _refs_present(cu, "ContractApplied"):
        diagnostics.append(
            _note(
                "FDT-BUNDLE-001",
                Severity.NOT_EXECUTABLE,
                "Cost and Usage ContractApplied references cannot be checked: the Contract "
                "Commitment dataset is absent from the bundle",
                ("Cost and Usage", "Contract Commitment"),
            )
        )
    if has_cu and has_bp:
        run(
            "billing_period_coverage",
            referential.check_billing_period_coverage(cu, bp, index_factory=factory),
        )

    # Reconciliation (only for an authoritative Invoice Detail).
    if has_cu and has_invd:
        if invoice_detail_authoritative:
            tolerance = (
                rounding_tolerance
                if rounding_tolerance is not None
                else reconciliation.DEFAULT_TOLERANCE
            )
            run(
                "reconcile_invoice_detail",
                reconciliation.reconcile_invoice_detail(
                    cu, invd, tolerance=tolerance, index_factory=factory
                ),
            )
        else:
            diagnostics.append(
                _note(
                    "FDT-BUNDLE-002",
                    Severity.NOT_APPLICABLE,
                    "Cost and Usage <-> Invoice Detail reconciliation skipped: Invoice Detail is "
                    "not marked authoritative (a toolkit-derived Invoice Detail reconciles by "
                    "construction)",
                    ("Cost and Usage", "Invoice Detail"),
                )
            )
    elif invoice_detail_authoritative:
        diagnostics.append(
            _note(
                "FDT-BUNDLE-001",
                Severity.NOT_EXECUTABLE,
                "reconciliation not executable: Cost and Usage or Invoice Detail is absent",
                ("Cost and Usage", "Invoice Detail"),
            )
        )

    # Split cost allocation and correction integrity (self-contained within Cost and Usage).
    if has_cu:
        run(
            "split_allocation",
            allocation.validate_split_allocation(cu, index_factory=factory),
        )
        run(
            "correction_references",
            corrections.check_correction_references(cu, index_factory=factory),
        )
        run(
            "correction_net_sums",
            corrections.check_correction_net_sums(cu, index_factory=factory),
        )
        run(
            "no_duplicate_charge_keys",
            corrections.check_no_duplicate_charge_keys(cu, index_factory=factory),
        )

    # Commitment lifecycle.
    if has_cc:
        run("contract_commitment_periods", corrections.check_contract_commitment_periods(cc))
        run(
            "contract_commitment_percentages",
            corrections.check_contract_commitment_percentages(cc),
        )

    return BundleReport(diagnostics=diagnostics, checks_run=tuple(checks))


__all__ = ["Bundle", "BundleReport", "validate_dataset_bundle"]
