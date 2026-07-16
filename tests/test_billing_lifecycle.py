"""Billing lifecycle (P1.10): dataset-instance snapshots and status-transition checks."""

from __future__ import annotations

import dataclasses

from focus_data_toolkit.generators.scenarios import billing_lifecycle_instances
from focus_data_toolkit.lifecycle import (
    DatasetInstance,
    check_billing_period_status_transitions,
    check_invoice_status_transitions,
    check_status_transitions,
)


def _codes(diags) -> set[str]:
    return {d.code for d in diags}


def test_canonical_lifecycle_has_only_allowed_transitions():
    instances = billing_lifecycle_instances()
    assert check_status_transitions(instances) == []


def test_lifecycle_records_period_open_to_closed_and_invoice_issue_void_replace():
    instances = billing_lifecycle_instances()
    statuses = [(i.instance_id, i.dataset, i.status) for i in instances]
    assert statuses == [
        ("t0", "Billing Period", "Open"),
        ("t1", "Billing Period", "Open"),
        ("t2", "Billing Period", "Closed"),
        ("t3", "Invoice Detail", "Issued"),
        ("t4", "Invoice Detail", "Issued"),
        ("t5", "Invoice Detail", "Voided"),
        ("t6", "Invoice Detail", "Issued"),  # replacement invoice (new subject id)
    ]
    # The replacement is a distinct subject, so t5->t6 is not a Voided->Issued transition.
    assert instances[-1].subject_id != instances[-2].subject_id


def test_dataset_instance_carries_completeness_and_lineage():
    t0, t1 = billing_lifecycle_instances()[:2]
    assert t0.complete is False and t0.previous_instance_id is None
    assert t1.previous_instance_id == "t0"
    assert t0.period_start and t0.period_end and t0.created and t0.last_updated


def _invoice(instance_id, order, status, prev, subject="INV-1"):
    return DatasetInstance(
        instance_id=instance_id,
        dataset="Invoice Detail",
        subject_id=subject,
        order=order,
        created="2026-01-01T00:00:00Z",
        last_updated="2026-02-01T00:00:00Z",
        complete=True,
        period_start="2026-01-01T00:00:00Z",
        period_end="2026-02-01T00:00:00Z",
        status=status,
        previous_instance_id=prev,
    )


def test_voided_invoice_cannot_return_to_issued():
    instances = [
        _invoice("a", 0, "Issued", None),
        _invoice("b", 1, "Voided", "a"),
        _invoice("c", 2, "Issued", "b"),  # illegal un-void
    ]
    assert "FDT-CORR-004" in _codes(check_invoice_status_transitions(instances))


def test_issued_invoice_cannot_revert_to_open():
    instances = [_invoice("a", 0, "Issued", None), _invoice("b", 1, "Open", "a")]
    assert "FDT-CORR-004" in _codes(check_invoice_status_transitions(instances))


def test_reopening_a_closed_billing_period_is_flagged():
    period = [
        DatasetInstance("a", "Billing Period", "2026-01", 0, "c", "u", True, "s", "e", "Closed", None),
        DatasetInstance("b", "Billing Period", "2026-01", 1, "c", "u", True, "s", "e", "Open", "a"),
    ]
    assert "FDT-CORR-004" in _codes(check_billing_period_status_transitions(period))


def test_transitions_are_scoped_per_subject():
    # Two invoices interleaved: each is internally legal, so no false cross-subject transition.
    instances = [
        _invoice("a0", 0, "Open", None, subject="INV-A"),
        _invoice("b0", 1, "Open", None, subject="INV-B"),
        _invoice("a1", 2, "Issued", "a0", subject="INV-A"),
        _invoice("b1", 3, "Issued", "b0", subject="INV-B"),
    ]
    assert check_invoice_status_transitions(instances) == []


def test_instances_are_deterministic():
    assert billing_lifecycle_instances() == billing_lifecycle_instances()
    assert dataclasses.astuple(billing_lifecycle_instances()[0]) == dataclasses.astuple(
        billing_lifecycle_instances()[0]
    )
