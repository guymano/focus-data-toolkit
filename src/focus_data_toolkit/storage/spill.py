"""Threshold-spilling string maps for streaming cross-dataset validation.

The bundle validator's checks keep per-key state (seen ids, foreign-key targets, running
sums) whose cardinality scales with the *large* Cost and Usage dataset. A
:class:`SpillableIndexPool` hands out ``str -> str`` mutable mappings that live in an
ordinary in-memory ``dict`` until a size threshold, then migrate transparently into a shared
throwaway SQLite database — so validating a bundle far larger than RAM stays bounded.

Like :mod:`focus_data_toolkit.storage.external_index`, the database is disposable scratch
state (``journal_mode=OFF``, ``synchronous=OFF``): durability comes from the atomic writer's
fsync of the published files, never from this DB. The file is created lazily on the first
spill, so small bundles touch the disk not at all.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, MutableMapping
from pathlib import Path

# Keys held in memory per map before spilling to SQLite. At ~100 bytes per key/value pair
# this bounds each map's resident size to roughly 20 MB worst-case before it moves to disk.
DEFAULT_SPILL_THRESHOLD = 200_000


class SpillableIndexPool:
    """Factory of :class:`SpillableMap` instances sharing one lazy scratch SQLite database."""

    def __init__(self, db_path: str | Path, *, threshold: int = DEFAULT_SPILL_THRESHOLD) -> None:
        if threshold < 1:
            raise ValueError(f"spill threshold must be >= 1, got {threshold}")
        self._db_path = Path(db_path)
        self._threshold = threshold
        self._conn: sqlite3.Connection | None = None
        self._tables = 0
        self._spilled = False

    @property
    def threshold(self) -> int:
        return self._threshold

    @property
    def spilled(self) -> bool:
        """Whether any map has ever spilled (i.e. the scratch database was created)."""
        return self._spilled

    def make_map(self) -> SpillableMap:
        """Return a fresh empty ``str -> str`` mapping backed by this pool."""
        self._tables += 1
        return SpillableMap(self, f"kv{self._tables}")

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            self._spilled = True
            for pragma in (
                "journal_mode=OFF",
                "synchronous=OFF",
                "temp_store=FILE",
                "cache_size=-20000",
            ):
                self._conn.execute(f"PRAGMA {pragma}")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


class SpillableMap(MutableMapping[str, str]):
    """A ``str -> str`` mapping that spills from a dict to the pool's SQLite past a threshold."""

    def __init__(self, pool: SpillableIndexPool, table: str) -> None:
        self._pool = pool
        self._table = table
        self._mem: dict[str, str] | None = {}

    def _spill(self) -> None:
        assert self._mem is not None
        conn = self._pool._connection()
        conn.execute(f"CREATE TABLE {self._table} (k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID")
        conn.executemany(
            f"INSERT INTO {self._table} (k, v) VALUES (?, ?)", self._mem.items()
        )
        self._mem = None

    def __setitem__(self, key: str, value: str) -> None:
        if self._mem is not None:
            self._mem[key] = value
            if len(self._mem) > self._pool.threshold:
                self._spill()
            return
        self._pool._connection().execute(
            f"INSERT OR REPLACE INTO {self._table} (k, v) VALUES (?, ?)", (key, value)
        )

    def __getitem__(self, key: str) -> str:
        if self._mem is not None:
            return self._mem[key]
        row = (
            self._pool._connection()
            .execute(f"SELECT v FROM {self._table} WHERE k = ?", (key,))
            .fetchone()
        )
        if row is None:
            raise KeyError(key)
        return row[0]

    def __delitem__(self, key: str) -> None:
        if self._mem is not None:
            del self._mem[key]
            return
        cursor = self._pool._connection().execute(
            f"DELETE FROM {self._table} WHERE k = ?", (key,)
        )
        if cursor.rowcount == 0:
            raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        if self._mem is not None:
            return key in self._mem
        if not isinstance(key, str):
            return False
        row = (
            self._pool._connection()
            .execute(f"SELECT 1 FROM {self._table} WHERE k = ?", (key,))
            .fetchone()
        )
        return row is not None

    def __len__(self) -> int:
        if self._mem is not None:
            return len(self._mem)
        return self._pool._connection().execute(
            f"SELECT COUNT(*) FROM {self._table}"
        ).fetchone()[0]

    def __iter__(self) -> Iterator[str]:
        if self._mem is not None:
            yield from self._mem
            return
        # Sorted (BINARY collation = UTF-8 byte order) for a deterministic iteration order.
        for (key,) in self._pool._connection().execute(
            f"SELECT k FROM {self._table} ORDER BY k"
        ):
            yield key


__all__ = ["DEFAULT_SPILL_THRESHOLD", "SpillableIndexPool", "SpillableMap"]
