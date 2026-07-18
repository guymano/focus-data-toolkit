"""Bounded, paginated preview of a produced file — never materialises the whole dataset."""

from __future__ import annotations

import contextlib
from pathlib import Path

from focus_data_toolkit.io.row_source import open_row_source


def sampled_page(
    path: str | Path, *, dataset: str | None = None, offset: int = 0, limit: int = 50
) -> dict:
    """Return ``limit`` rows starting at ``offset`` from ``path`` (CSV/Parquet, incl. partitioned).

    Reads at most ``offset + limit`` rows and stops — memory is bounded regardless of file size,
    so a multi-GB result previews the same way a tiny one does. The full file is never loaded into
    the backend or sent to the browser.
    """
    offset = max(0, offset)
    limit = max(1, limit)
    columns: list[str] = []
    rows: list[dict[str, str]] = []
    with contextlib.closing(open_row_source(path, dataset=dataset)) as reader:
        columns = list(reader.source_columns)
        for index, record in enumerate(reader):
            if index < offset:
                continue
            if len(rows) >= limit:
                break
            rows.append(dict(record.values))
    return {"columns": columns, "rows": rows, "offset": offset, "limit": limit}
