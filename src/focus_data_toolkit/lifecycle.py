"""Dataset-instance snapshots and billing/invoice status-transition rules (P1.10).

A **dataset instance** is one delivered snapshot of a FOCUS dataset for a subject (an invoice,
a billing period): who it is, when it was created / last updated, whether it is complete, the
period it covers, its lifecycle status, and which instance preceded it. Real client pipelines
*record* these (they are never fabricated for client data); the toolkit models them so a
sequence of snapshots can be checked for illegal status transitions — e.g. a ``Voided`` invoice
must not silently go back to ``Issued``, an ``Issued`` invoice must not revert to ``Open``.

The allowed transitions come straight from the FOCUS ``InvoiceIssueStatus`` (``Open`` →
``Issued`` → ``Voided``) and ``BillingPeriodStatus`` (``Open`` → ``Closed``) value sets; a
correction is a *new* instance/line, never an in-place edit of a closed/issued one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from focus_data_toolkit.errors import Diagnostic, Severity

# Allowed forward transitions (a status may also stay the same — an idempotent re-delivery).
INVOICE_TRANSITIONS: dict[str, frozenset[str]] = {
    "Open": frozenset({"Open", "Issued", "Voided"}),
    "Issued": frozenset({"Issued", "Voided"}),
    "Voided": frozenset({"Voided"}),
}
BILLING_PERIOD_TRANSITIONS: dict[str, frozenset[str]] = {
    "Open": frozenset({"Open", "Closed"}),
    "Closed": frozenset({"Closed"}),
}


@dataclass(frozen=True)
class DatasetInstance:
    """One recorded snapshot of a dataset for a subject (invoice / billing period)."""

    instance_id: str
    dataset: str
    subject_id: str
    order: int
    created: str
    last_updated: str
    complete: bool
    period_start: str
    period_end: str
    status: str
    previous_instance_id: str | None = None


def _check_transitions(
    instances: Sequence[DatasetInstance], allowed: dict[str, frozenset[str]]
) -> list[Diagnostic]:
    """Flag any status change not permitted by ``allowed`` between consecutive snapshots.

    Snapshots are grouped by ``subject_id`` and ordered by ``order``; each adjacent pair is a
    transition. An unknown status value is left to the per-dataset linter (allowed-value check).
    """
    by_subject: dict[str, list[DatasetInstance]] = {}
    for inst in instances:
        by_subject.setdefault(inst.subject_id, []).append(inst)

    out: list[Diagnostic] = []
    for subject, snaps in sorted(by_subject.items()):
        ordered = sorted(snaps, key=lambda s: s.order)
        for prev, cur in zip(ordered, ordered[1:], strict=False):
            permitted = allowed.get(prev.status)
            if permitted is None or cur.status not in permitted:
                out.append(
                    Diagnostic(
                        code="FDT-CORR-004",
                        severity=Severity.ERROR,
                        message=f"illegal status transition {prev.status!r} -> {cur.status!r} "
                        f"for {subject!r} (instances {prev.instance_id} -> {cur.instance_id})",
                        datasets=(cur.dataset,),
                        dataset=cur.dataset,
                        record_keys={"subject_id": subject},
                        expected=f"one of {sorted(permitted)}" if permitted else "known status",
                        actual=cur.status,
                        context={"from_instance": prev.instance_id, "to_instance": cur.instance_id},
                    )
                )
    return out


def check_invoice_status_transitions(instances: Iterable[DatasetInstance]) -> list[Diagnostic]:
    """Validate Invoice Detail status transitions across dataset instances (FDT-CORR-004)."""
    invoices = [i for i in instances if i.dataset == "Invoice Detail"]
    return _check_transitions(invoices, INVOICE_TRANSITIONS)


def check_billing_period_status_transitions(
    instances: Iterable[DatasetInstance],
) -> list[Diagnostic]:
    """Validate Billing Period status transitions across dataset instances (FDT-CORR-004)."""
    periods = [i for i in instances if i.dataset == "Billing Period"]
    return _check_transitions(periods, BILLING_PERIOD_TRANSITIONS)


def check_status_transitions(instances: Iterable[DatasetInstance]) -> list[Diagnostic]:
    """Validate both invoice and billing-period status transitions in one pass."""
    instances = list(instances)
    return check_invoice_status_transitions(instances) + check_billing_period_status_transitions(
        instances
    )


__all__ = [
    "BILLING_PERIOD_TRANSITIONS",
    "INVOICE_TRANSITIONS",
    "DatasetInstance",
    "check_billing_period_status_transitions",
    "check_invoice_status_transitions",
    "check_status_transitions",
]
