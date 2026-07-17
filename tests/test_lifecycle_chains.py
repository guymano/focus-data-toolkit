"""Lifecycle chain-structure validation (PR-12): FDT-LIFE-001..006 diagnostics."""

from __future__ import annotations

import dataclasses

from focus_data_toolkit.generators.scenarios import billing_lifecycle_instances
from focus_data_toolkit.lifecycle import (
    DatasetInstance,
    check_dataset_instances,
    check_instance_chains,
)


def _codes(diags) -> list[str]:
    return sorted(d.code for d in diags)


def snap(
    instance_id: str,
    order: int,
    *,
    subject: str = "INV-1",
    dataset: str = "Invoice Detail",
    status: str = "Open",
    created: str = "2026-01-01T00:00:00Z",
    updated: str = "2026-01-02T00:00:00Z",
    complete: bool = False,
    start: str = "2026-01-01T00:00:00Z",
    end: str = "2026-02-01T00:00:00Z",
    prev: str | None = None,
) -> DatasetInstance:
    return DatasetInstance(
        instance_id=instance_id, dataset=dataset, subject_id=subject, order=order,
        created=created, last_updated=updated, complete=complete,
        period_start=start, period_end=end, status=status, previous_instance_id=prev,
    )


def test_canonical_scenario_passes_the_full_battery():
    assert check_dataset_instances(billing_lifecycle_instances()) == []


def test_duplicate_instance_id_is_flagged():
    diags = check_instance_chains([snap("a", 0), snap("a", 1, prev="a")])
    assert "FDT-LIFE-001" in _codes(diags)


def test_duplicate_order_per_subject_is_flagged():
    diags = check_instance_chains([snap("a", 0), snap("b", 0)])
    assert _codes(diags) == ["FDT-LIFE-002"]
    # Same order on different subjects is fine.
    assert check_instance_chains([snap("a", 0), snap("b", 0, subject="INV-2")]) == []


def test_unresolved_previous_instance_id():
    diags = check_instance_chains([snap("b", 1, prev="ghost")])
    assert _codes(diags) == ["FDT-LIFE-003"]


def test_self_referencing_previous_instance_id():
    diags = check_instance_chains([snap("b", 1, prev="b")])
    assert "FDT-LIFE-003" in _codes(diags)


def test_previous_link_may_cross_subjects():
    # Delivery lineage legitimately crosses subjects (e.g. replacement invoice -> voided one).
    diags = check_instance_chains([snap("a", 0, subject="INV-2"), snap("b", 1, prev="a")])
    assert diags == []


def test_previous_link_must_precede_in_order():
    diags = check_instance_chains([snap("a", 2), snap("b", 1, prev="a")])
    assert _codes(diags) == ["FDT-LIFE-003"]


def test_cycle_in_previous_links_is_reported_once():
    diags = check_instance_chains([
        snap("a", 0, prev="b"),  # a -> b -> a
        snap("b", 1, prev="a"),
    ])
    # The backwards link also violates order precedence; the cycle itself is one FDT-LIFE-004.
    assert _codes(diags).count("FDT-LIFE-004") == 1


def test_last_updated_before_created_is_flagged():
    diags = check_instance_chains(
        [snap("a", 0, created="2026-01-05T00:00:00Z", updated="2026-01-01T00:00:00Z")]
    )
    assert _codes(diags) == ["FDT-LIFE-005"]


def test_last_updated_moving_backwards_is_flagged():
    diags = check_instance_chains([
        snap("a", 0, updated="2026-01-10T00:00:00Z"),
        snap("b", 1, updated="2026-01-05T00:00:00Z", prev="a"),
    ])
    assert _codes(diags) == ["FDT-LIFE-005"]


def test_malformed_timestamps_are_left_to_the_linter():
    diags = check_instance_chains([
        snap("a", 0, updated="not-a-date"),
        snap("b", 1, created="also-bad", updated="2026-01-05T00:00:00Z", prev="a"),
    ])
    assert diags == []


def test_closed_snapshot_freezes_the_period():
    diags = check_instance_chains([
        snap("a", 0, status="Issued", complete=True),
        snap("b", 1, status="Issued", complete=True,
             end="2026-03-01T00:00:00Z", prev="a",
             updated="2026-01-03T00:00:00Z"),
    ])
    assert _codes(diags) == ["FDT-LIFE-006"]


def test_completeness_must_not_regress_after_close():
    diags = check_instance_chains([
        snap("a", 0, status="Closed", dataset="Billing Period", subject="BP-1", complete=True),
        snap("b", 1, status="Closed", dataset="Billing Period", subject="BP-1",
             complete=False, prev="a", updated="2026-01-03T00:00:00Z"),
    ])
    assert _codes(diags) == ["FDT-LIFE-006"]


def test_full_battery_includes_status_transitions():
    instances = billing_lifecycle_instances()
    voided_then_issued = [
        *instances,
        dataclasses.replace(
            instances[-2],  # the Voided invoice snapshot
            instance_id="t7",
            order=instances[-2].order + 1,
            status="Issued",
            previous_instance_id=instances[-2].instance_id,
        ),
    ]
    codes = _codes(check_dataset_instances(voided_then_issued))
    assert "FDT-CORR-004" in codes  # Voided -> Issued is illegal
