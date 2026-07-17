"""Coherent, provider-agnostic FOCUS scenario generators (P1.8 / P1.9 / P1.10).

Unlike the large per-provider generators (which emit whole Cost and Usage files), these build
small, *self-consistent* scenarios that exercise the cross-dataset validators on realistic —
not random — data:

* :func:`split_allocation_group` — a Split Cost Allocation group whose ratios sum to exactly 1
  and whose allocated costs sum to exactly the origin charge (the last consumer absorbs the
  rounding residue), so it passes :func:`~focus_data_toolkit.validate.allocation.validate_split_allocation`.
* :func:`correction_set` — an original charge plus a signed correction line
  (``ChargeClass="Correction"``) that references it and records the auditable net charge.
* :func:`billing_lifecycle_instances` — a T0→T6 sequence of
  :class:`~focus_data_toolkit.lifecycle.DatasetInstance` snapshots (period opens, fills, closes;
  invoice is issued, voided, replaced) with only *allowed* status transitions.

Everything is deterministic (no RNG, no clock): the same arguments always yield the same rows,
so the scenarios are byte-reproducible and safe to snapshot in tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from focus_data_toolkit.generators.engine.json_focus import allocated_method_details
from focus_data_toolkit.lifecycle import DatasetInstance

_RATIO_QUANTUM = Decimal("0.000001")
_COST_QUANTUM = Decimal("0.000001")


def split_allocation_group(
    origin_id: str,
    origin_cost: str | Decimal,
    weights: Sequence[float | int | str | Decimal],
    *,
    method: str = "split-proportional",
    unit: str = "Hours",
    resource_prefix: str = "res",
) -> list[dict[str, str]]:
    """Build a valid Split Cost Allocation group distributing ``origin_cost`` over ``weights``.

    Ratios sum to exactly 1 and allocated ``BilledCost`` sums to exactly ``origin_cost``: each
    consumer gets ``weight/total`` (quantised), and the **last** consumer takes the residue so
    no cents are created or lost. ``weights`` may encode equal (all 1), proportional, or
    weighted allocation, and may be negative for a correction that redistributes a credit.
    """
    if len(weights) < 1:
        raise ValueError("an allocation group needs at least one consumer")
    origin = Decimal(str(origin_cost))
    decimal_weights = [Decimal(str(w)) for w in weights]
    total = sum(decimal_weights, Decimal(0))
    if total == 0:
        raise ValueError("allocation weights sum to zero; cannot form ratios")

    ratios: list[Decimal] = []
    costs: list[Decimal] = []
    acc_ratio = Decimal(0)
    acc_cost = Decimal(0)
    last = len(decimal_weights) - 1
    for i, w in enumerate(decimal_weights):
        if i < last:
            ratio = (w / total).quantize(_RATIO_QUANTUM)
            cost = (origin * w / total).quantize(_COST_QUANTUM)
            acc_ratio += ratio
            acc_cost += cost
        else:  # last consumer absorbs the residue so both sums stay exact
            ratio = Decimal(1) - acc_ratio
            cost = origin - acc_cost
        ratios.append(ratio)
        costs.append(cost)

    return [
        {
            "x_SplitOriginId": origin_id,
            "x_SplitOriginCost": str(origin),
            "AllocatedMethodId": method,
            "AllocatedResourceId": f"{resource_prefix}-{i}",
            "AllocatedMethodDetails": allocated_method_details(
                [{"AllocatedRatio": str(ratios[i]), "UsageUnit": unit, "UsageQuantity": "1"}]
            ),
            "BilledCost": str(costs[i]),
            "ChargeCategory": "Usage",
        }
        for i in range(len(decimal_weights))
    ]


def correction_set(
    charge_key: str,
    original_cost: str | Decimal,
    corrections: Sequence[str | Decimal],
    *,
    invoice_id: str = "INV-1",
    currency: str = "USD",
    charge_category: str = "Usage",
) -> list[dict[str, str]]:
    """An original charge plus one signed correction line per entry in ``corrections``.

    The original keeps its own ``x_ChargeKey`` (never overwritten); each correction is a
    ``ChargeClass="Correction"`` line that references the original via ``x_CorrectionOf`` and
    records the running auditable net (``x_NetCharge`` = original + corrections so far). The net
    of the whole set is therefore verifiable and no history is destroyed.
    """
    original = Decimal(str(original_cost))
    rows = [
        {
            "x_ChargeKey": charge_key,
            "ChargeClass": "",
            "ChargeCategory": charge_category,
            "BilledCost": str(original),
            "InvoiceId": invoice_id,
            "BillingCurrency": currency,
        }
    ]
    running = original
    for n, amount in enumerate(corrections, start=1):
        delta = Decimal(str(amount))
        running += delta
        rows.append(
            {
                "x_ChargeKey": f"{charge_key}-c{n}",
                "x_CorrectionOf": charge_key,
                "ChargeClass": "Correction",
                "ChargeCategory": "Credit" if delta < 0 else charge_category,
                "BilledCost": str(delta),
                "x_NetCharge": str(running),
                "InvoiceId": invoice_id,
                "BillingCurrency": currency,
            }
        )
    return rows


def billing_lifecycle_instances(
    *,
    invoice_id: str = "INV-1",
    period_start: str = "2026-01-01T00:00:00Z",
    period_end: str = "2026-02-01T00:00:00Z",
    replacement_invoice_id: str = "INV-1-R1",
) -> list[DatasetInstance]:
    """A canonical T0→T6 billing lifecycle as a sequence of dataset-instance snapshots.

    T0 period opens (incomplete) → T1 more Cost and Usage arrives → T2 period closes → T3
    invoice issued → T4 late correction recorded → T5 invoice voided → T6 replacement issued.
    Only allowed status transitions occur; each snapshot links to the previous one.
    """
    steps = [
        ("t0", "Billing Period", False, "Open", None),
        ("t1", "Billing Period", False, "Open", "t0"),
        ("t2", "Billing Period", True, "Closed", "t1"),
        ("t3", "Invoice Detail", True, "Issued", "t2"),
        ("t4", "Invoice Detail", True, "Issued", "t3"),  # correction recorded, still issued
        ("t5", "Invoice Detail", True, "Voided", "t4"),
        ("t6", "Invoice Detail", True, "Issued", "t5"),  # replacement invoice issued
    ]
    instances: list[DatasetInstance] = []
    for order, (instance_id, dataset, complete, status, prev) in enumerate(steps):
        inv = replacement_invoice_id if instance_id == "t6" else invoice_id
        instances.append(
            DatasetInstance(
                instance_id=instance_id,
                dataset=dataset,
                subject_id=inv,
                order=order,
                created=period_start,
                last_updated=period_end,
                complete=complete,
                period_start=period_start,
                period_end=period_end,
                status=status,
                previous_instance_id=prev,
            )
        )
    return instances


__all__ = ["billing_lifecycle_instances", "correction_set", "split_allocation_group"]
