"""Progress reporting + cooperative cancellation in the streaming engine (Lot A / A1)."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from focus_data_toolkit.convert import ConversionCancelled, convert_files
from focus_data_toolkit.generators import get_generator
from focus_data_toolkit.io.csv_io import CsvRowReader
from focus_data_toolkit.progress import PHASES, ProgressEvent


def _cau(tmp_path: Path, n: int = 1500, seed: int = 7) -> Path:
    src = tmp_path / "cau.csv"
    src.write_bytes(get_generator("aws", "1.3").generate_csv_bytes(n, seed))
    return src


def test_progress_events_cover_phases(tmp_path):
    events: list[ProgressEvent] = []
    convert_files(
        _cau(tmp_path), tmp_path / "out", mode="synthetic",
        progress=events.append, progress_interval=100,
    )
    assert events, "no progress events emitted"
    seen = {e.phase for e in events}
    assert seen <= set(PHASES)
    assert {"TRANSFORMING", "VALIDATING"} <= seen
    assert events[-1].phase == "PUBLISHING"  # publishing is always the final phase


def test_transform_progress_reports_byte_fraction(tmp_path):
    events: list[ProgressEvent] = []
    convert_files(
        _cau(tmp_path), tmp_path / "out", mode="synthetic",
        progress=events.append, progress_interval=100,
    )
    transforming = [e for e in events if e.phase == "TRANSFORMING" and e.completed > 0]
    assert transforming, "no in-progress TRANSFORMING events"
    assert all(e.unit == "bytes" for e in transforming)
    assert all(e.total and 0.0 <= (e.fraction or 0.0) <= 1.0 for e in transforming)


def test_progress_is_opt_in_and_output_unchanged(tmp_path):
    src = _cau(tmp_path)
    plain = tmp_path / "plain"
    convert_files(src, plain, mode="synthetic")
    withcb = tmp_path / "withcb"
    convert_files(src, withcb, mode="synthetic", progress=lambda e: None, progress_interval=50)
    # progress callbacks are operational only — business artifacts stay byte-identical
    assert (plain / "SHA256SUMS").read_bytes() == (withcb / "SHA256SUMS").read_bytes()


def test_cancel_leaves_no_output(tmp_path):
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # trip on the second check (mid TRANSFORMING)

    out = tmp_path / "out"
    with pytest.raises(ConversionCancelled):
        convert_files(_cau(tmp_path), out, mode="synthetic", cancel=cancel, progress_interval=50)
    assert not out.exists()
    assert not list(tmp_path.glob(".output.tmp-*"))  # staging removed
    assert not list(tmp_path.glob("*.sqlite"))  # scratch removed


def test_cancel_before_first_row(tmp_path):
    out = tmp_path / "out"
    with pytest.raises(ConversionCancelled):
        convert_files(_cau(tmp_path), out, mode="synthetic", cancel=lambda: True, progress_interval=50)
    assert not out.exists()


def test_cancel_closes_scratch_index(tmp_path, monkeypatch):
    # Regression (Windows): the SQLite scratch index must be closed on the cancel path before
    # staging is removed — Windows cannot rmtree a directory containing an open file.
    import focus_data_toolkit.convert.streaming as streaming

    real_opener = streaming.ExternalIndexOpener
    state = {"closed": False}

    class _Spy:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            state["closed"] = True
            self._inner.close()

    monkeypatch.setattr(streaming, "ExternalIndexOpener", lambda p: _Spy(real_opener(p)))
    with pytest.raises(ConversionCancelled):
        convert_files(
            _cau(tmp_path), tmp_path / "out", mode="synthetic",
            cancel=lambda: True, progress_interval=50,
        )
    assert state["closed"], "scratch index must be closed on cancel before staging cleanup"


def test_csv_reader_byte_accessors(tmp_path):
    src = _cau(tmp_path, n=400)
    reader = CsvRowReader(src)
    assert reader.bytes_total == src.stat().st_size
    iterator = iter(reader)
    next(iterator)
    first = reader.bytes_read
    assert first is not None and first > 0
    for _ in range(200):
        if next(iterator, None) is None:
            break
    assert reader.bytes_read is not None and reader.bytes_read >= first
    reader.close()


def test_gzip_reader_byte_accessors(tmp_path):
    raw = get_generator("aws", "1.3").generate_csv_bytes(400, 7)
    src = tmp_path / "cau.csv.gz"
    src.write_bytes(gzip.compress(raw))
    reader = CsvRowReader(src)
    assert reader.bytes_total == src.stat().st_size  # compressed size
    iterator = iter(reader)
    next(iterator)
    pos = reader.bytes_read
    # compressed offset (or None if unavailable); never exceeds the compressed file size
    assert pos is None or (isinstance(pos, int) and 0 <= pos <= reader.bytes_total)
    reader.close()


def test_parquet_reader_expected_rows(tmp_path):
    pytest.importorskip("pyarrow")
    outdir = tmp_path / "pq"
    convert_files(_cau(tmp_path, n=300), outdir, mode="synthetic", output_format="parquet")
    from focus_data_toolkit.io.parquet_io import ParquetRowReader

    parquet_file = next(outdir.glob("*cost_and_usage.parquet"))
    reader = ParquetRowReader(parquet_file, dataset="Cost and Usage")
    assert reader.expected_rows == 300
    reader.close()


def test_progress_totals_skips_recount_without_callback():
    # Regression (PR #23): without a progress callback the engine must not touch expected_rows
    # (count_rows() is expensive for partitioned Parquet).
    from focus_data_toolkit.convert.streaming import _progress_totals

    class _Boom:
        bytes_total = None

        @property
        def expected_rows(self):
            raise AssertionError("expected_rows must not be read without a progress callback")

    assert _progress_totals(_Boom(), None) == ("rows", None)

    class _Reader:
        bytes_total = None
        expected_rows = 42

    assert _progress_totals(_Reader(), lambda _e: None) == ("rows", 42)
