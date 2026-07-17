"""Disk-backed aggregation/dedup for streaming conversion, using stdlib ``sqlite3``.

Streaming the huge Cost and Usage file still needs a little global state — the Invoice Detail
sum per business grain and the distinct Billing Periods. Holding that in Python dicts would
scale with the number of *groups*; here it lives in a throwaway SQLite database inside the
atomic staging directory, so memory stays bounded.

Exactness and determinism:

* Costs are stored as **TEXT** and summed with Python ``Decimal`` during the ordered scan —
  never ``SUM()`` in SQL — so the streamed sum is bit-for-bit the eager sum.
* Every finalize scan is ``ORDER BY <keys>`` under the default **BINARY** collation, which
  compares UTF-8 bytes and so matches Python ``sorted()`` on the same string tuples.

The scratch database is disposable (``journal_mode=OFF``, ``synchronous=OFF``); durability
comes from the atomic writer's fsync of the finished data files, not from this DB.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

from focus_data_toolkit.convert.invoice_detail import GrainKey

# Invoice Detail business-grain columns, in key order (matches GRAIN_FIELDS).
_GRAIN_COLS = (
    "issuer",
    "invoice_id",
    "account",
    "currency",
    "bp_start",
    "bp_end",
    "charge_category",
)


class ExternalIndex:
    """SQLite-backed staging for Invoice Detail aggregation and Billing Period dedup."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn = sqlite3.connect(str(db_path))
        for pragma in ("journal_mode=OFF", "synchronous=OFF", "temp_store=FILE", "cache_size=-20000"):
            self._conn.execute(f"PRAGMA {pragma}")
        self._conn.execute(
            "CREATE TABLE id_stage (n INTEGER PRIMARY KEY, "
            + ", ".join(f"{col} TEXT" for col in _GRAIN_COLS)
            + ", billed_cost TEXT)"
        )
        self._conn.execute(
            "CREATE TABLE bp (start TEXT, end TEXT, issuer TEXT, "
            "PRIMARY KEY (start, end, issuer)) WITHOUT ROWID"
        )
        self._insert_line = (
            "INSERT INTO id_stage (" + ", ".join(_GRAIN_COLS) + ", billed_cost) VALUES ("
            + ", ".join("?" * (len(_GRAIN_COLS) + 1)) + ")"
        )

    def stage_invoice_line(self, grain_key: GrainKey, billed_cost: str) -> None:
        """Record one Cost and Usage line's contribution to its invoice-detail group."""
        self._conn.execute(self._insert_line, (*grain_key, billed_cost))

    def stage_billing_period(self, start: str, end: str, issuer: str) -> None:
        """Record a (start, end, issuer) billing period (first occurrence wins)."""
        self._conn.execute(
            "INSERT OR IGNORE INTO bp (start, end, issuer) VALUES (?, ?, ?)", (start, end, issuer)
        )

    def finalize_invoice_groups(self) -> Iterator[tuple[GrainKey, Decimal]]:
        """Yield ``(grain_key, summed_billed_cost)`` per group, in sorted grain order."""
        self._conn.commit()
        order = ", ".join(_GRAIN_COLS) + ", n"
        cursor = self._conn.execute(
            f"SELECT {', '.join(_GRAIN_COLS)}, billed_cost FROM id_stage ORDER BY {order}"
        )
        current: GrainKey | None = None
        total = Decimal(0)
        for row in cursor:
            key: GrainKey = tuple(row[: len(_GRAIN_COLS)])
            cost = row[len(_GRAIN_COLS)]
            if current is None:
                current = key
            if key != current:
                yield current, total
                current = key
                total = Decimal(0)
            total += Decimal(cost or "0")
        if current is not None:
            yield current, total

    def finalize_billing_periods(self) -> Iterator[tuple[str, str, str]]:
        """Yield distinct ``(start, end, issuer)`` billing periods, in sorted order."""
        self._conn.commit()
        yield from self._conn.execute("SELECT start, end, issuer FROM bp ORDER BY start, end, issuer")

    def close(self) -> None:
        self._conn.close()
