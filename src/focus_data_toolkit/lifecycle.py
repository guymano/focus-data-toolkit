"""Dataset-instance snapshots and billing/invoice lifecycle-chain rules (P1.10).

A **dataset instance** is one delivered snapshot of a FOCUS dataset for a subject (an invoice,
a billing period): who it is, when it was created / last updated, whether it is complete, the
period it covers, its lifecycle status, and which instance preceded it. Real client pipelines
*record* these (they are never fabricated for client data); the toolkit models them so a
sequence of snapshots can be checked for illegal status transitions — e.g. a ``Voided`` invoice
must not silently go back to ``Issued``, an ``Issued`` invoice must not revert to ``Open``.

The allowed transitions come straight from the FOCUS ``InvoiceIssueStatus`` (``Open`` →
``Issued`` → ``Voided``) and ``BillingPeriodStatus`` (``Open`` → ``Closed``) value sets; a
correction is a *new* instance/line, never an in-place edit of a closed/issued one.

Beyond transitions, :func:`check_instance_chains` validates the chain structure itself —
``instance_id`` / per-subject ``order`` uniqueness (FDT-LIFE-001/002),
``previous_instance_id`` resolution (FDT-LIFE-003), cycle detection (FDT-LIFE-004),
``last_updated`` monotonicity (FDT-LIFE-005), and closed-instance immutability
(FDT-LIFE-006) — all as structured diagnostics. :func:`check_dataset_instances` runs the
whole battery (chains + status transitions) in one call.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

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


# Statuses after which a subject's recorded facts are frozen: later snapshots may re-deliver
# or move forward in status, but must not rewrite the period or un-complete the data.
_CLOSED_STATUSES: frozenset[str] = frozenset({"Closed", "Issued", "Voided"})


def _parse_dt(value: str) -> datetime | None:
    """Parse a FOCUS Date/Time; naive or malformed values return None (the linter's job)."""
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _chain_diag(
    code: str, message: str, inst: DatasetInstance, **extra: object
) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity=Severity.ERROR,
        message=message,
        datasets=(inst.dataset,),
        dataset=inst.dataset,
        record_keys={"subject_id": inst.subject_id, "instance_id": inst.instance_id},
        **extra,  # type: ignore[arg-type]
    )


def check_instance_chains(instances: Iterable[DatasetInstance]) -> list[Diagnostic]:
    """Validate the structure of dataset-instance chains (FDT-LIFE-001..006).

    * **FDT-LIFE-001** — ``instance_id`` is reused across snapshots.
    * **FDT-LIFE-002** — two snapshots of one subject share the same ``order``.
    * **FDT-LIFE-003** — ``previous_instance_id`` does not resolve to an existing earlier
      snapshot (unknown id, self-reference, or non-decreasing order). Cross-subject and
      cross-dataset links are legitimate delivery lineage (e.g. a replacement invoice
      points at the voided one it replaces).
    * **FDT-LIFE-004** — the ``previous_instance_id`` links form a cycle.
    * **FDT-LIFE-005** — ``last_updated`` is before ``created``, or decreases between
      consecutive snapshots of a subject (history must move forward).
    * **FDT-LIFE-006** — a snapshot after a closed/issued/voided one rewrites the recorded
      period, or flips ``complete`` back to ``False`` (closed instances are immutable;
      a correction is a new line, never an in-place edit).

    Unknown status values and malformed timestamps are left to the per-dataset linter.
    """
    snaps = list(instances)
    out: list[Diagnostic] = []

    by_id: dict[str, DatasetInstance] = {}
    for inst in snaps:
        if inst.instance_id in by_id:
            out.append(
                _chain_diag(
                    "FDT-LIFE-001",
                    f"instance_id {inst.instance_id!r} is reused "
                    f"(subjects {by_id[inst.instance_id].subject_id!r} and {inst.subject_id!r})",
                    inst,
                    value=inst.instance_id,
                )
            )
        else:
            by_id[inst.instance_id] = inst

    by_subject: dict[tuple[str, str], list[DatasetInstance]] = {}
    for inst in snaps:
        by_subject.setdefault((inst.dataset, inst.subject_id), []).append(inst)

    for (_dataset, subject), group in sorted(by_subject.items()):
        seen_orders: dict[int, DatasetInstance] = {}
        for inst in group:
            if inst.order in seen_orders:
                out.append(
                    _chain_diag(
                        "FDT-LIFE-002",
                        f"snapshots {seen_orders[inst.order].instance_id!r} and "
                        f"{inst.instance_id!r} of {subject!r} share order {inst.order}",
                        inst,
                        actual=str(inst.order),
                    )
                )
            else:
                seen_orders[inst.order] = inst

        ordered = sorted(group, key=lambda s: (s.order, s.instance_id))

        # previous_instance_id resolution.
        for inst in ordered:
            prev_id = inst.previous_instance_id
            if prev_id is None:
                continue
            prev = by_id.get(prev_id)
            if prev is None or prev_id == inst.instance_id:
                reason = "references itself" if prev_id == inst.instance_id else "is unknown"
                out.append(
                    _chain_diag(
                        "FDT-LIFE-003",
                        f"previous_instance_id {prev_id!r} of {inst.instance_id!r} {reason}",
                        inst,
                        value=prev_id,
                    )
                )
            elif prev.order >= inst.order:
                out.append(
                    _chain_diag(
                        "FDT-LIFE-003",
                        f"previous_instance_id {prev_id!r} of {inst.instance_id!r} does not "
                        f"precede it (order {prev.order} >= {inst.order})",
                        inst,
                        value=prev_id,
                    )
                )

        # last_updated monotonicity along the delivery sequence.
        prev_updated: datetime | None = None
        prev_inst: DatasetInstance | None = None
        for inst in ordered:
            created, updated = _parse_dt(inst.created), _parse_dt(inst.last_updated)
            if created is not None and updated is not None and updated < created:
                out.append(
                    _chain_diag(
                        "FDT-LIFE-005",
                        f"last_updated of {inst.instance_id!r} precedes its created",
                        inst,
                        expected=f">= {inst.created}",
                        actual=inst.last_updated,
                    )
                )
            if updated is not None:
                if prev_updated is not None and updated < prev_updated:
                    assert prev_inst is not None
                    out.append(
                        _chain_diag(
                            "FDT-LIFE-005",
                            f"last_updated moves backwards between {prev_inst.instance_id!r} "
                            f"and {inst.instance_id!r} of {subject!r}",
                            inst,
                            expected=f">= {prev_inst.last_updated}",
                            actual=inst.last_updated,
                        )
                    )
                prev_updated, prev_inst = updated, inst

        # Closed-instance immutability: once closed/issued/voided, the recorded period is
        # frozen and completeness must not regress.
        frozen: DatasetInstance | None = None
        for inst in ordered:
            if frozen is not None:
                if (inst.period_start, inst.period_end) != (
                    frozen.period_start,
                    frozen.period_end,
                ):
                    out.append(
                        _chain_diag(
                            "FDT-LIFE-006",
                            f"{inst.instance_id!r} rewrites the period of {subject!r} recorded "
                            f"by closed snapshot {frozen.instance_id!r}",
                            inst,
                            expected=f"{frozen.period_start}/{frozen.period_end}",
                            actual=f"{inst.period_start}/{inst.period_end}",
                        )
                    )
                if frozen.complete and not inst.complete:
                    out.append(
                        _chain_diag(
                            "FDT-LIFE-006",
                            f"{inst.instance_id!r} flips {subject!r} back to incomplete after "
                            f"closed snapshot {frozen.instance_id!r}",
                            inst,
                            expected="complete=True",
                            actual="complete=False",
                        )
                    )
            if frozen is None and inst.status in _CLOSED_STATUSES:
                frozen = inst

    out.extend(_check_cycles(snaps, by_id))
    return out


def _check_cycles(
    snaps: Sequence[DatasetInstance], by_id: dict[str, DatasetInstance]
) -> list[Diagnostic]:
    """Detect cycles in the ``previous_instance_id`` graph (each reported once)."""
    out: list[Diagnostic] = []
    state: dict[str, int] = {}  # 0 = visiting, 1 = done
    for start in sorted(by_id):
        if start in state:
            continue
        path: list[str] = []
        current: str | None = start
        while current is not None and current in by_id and current not in state:
            state[current] = 0
            path.append(current)
            current = by_id[current].previous_instance_id
        if current is not None and state.get(current) == 0:
            cycle = path[path.index(current):]
            inst = by_id[current]
            out.append(
                _chain_diag(
                    "FDT-LIFE-004",
                    "previous_instance_id links form a cycle: " + " -> ".join([*cycle, current]),
                    inst,
                    context={"cycle": ",".join(cycle)},
                )
            )
        for node in path:
            state[node] = 1
    return out


def check_dataset_instances(instances: Iterable[DatasetInstance]) -> list[Diagnostic]:
    """Run the full lifecycle battery: chain structure plus status transitions."""
    snaps = list(instances)
    return check_instance_chains(snaps) + check_status_transitions(snaps)


__all__ = [
    "BILLING_PERIOD_TRANSITIONS",
    "INVOICE_TRANSITIONS",
    "DatasetInstance",
    "check_billing_period_status_transitions",
    "check_dataset_instances",
    "check_instance_chains",
    "check_invoice_status_transitions",
    "check_status_transitions",
]
