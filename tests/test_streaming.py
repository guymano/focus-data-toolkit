"""Streaming conversion (P1.5): equivalence with the eager path, bounded memory, robustness."""

from __future__ import annotations

import gzip
import json
import os
import tracemalloc
from decimal import Decimal
from pathlib import Path

import pytest

from focus_data_toolkit.convert import (
    ConversionError,
    convert_files,
    convert_to_focus_1_4,
    read_csv_rows,
    write_result,
)
from focus_data_toolkit.generators import get_generator
from focus_data_toolkit.io.records import MalformedRecordError


def _source(tmp_path: Path, *, provider: str = "aws", n: int = 200) -> tuple[Path, Path]:
    module = get_generator(provider, "1.3")
    cau = tmp_path / "cau.csv"
    cau.write_bytes(module.generate_csv_bytes(n, 1302))
    cc = tmp_path / "cc.csv"
    cc.write_bytes(module.generate_contract_commitment_csv_bytes(n, 1302))
    return cau, cc


def _csv_files(directory: Path) -> list[str]:
    return sorted(f for f in os.listdir(directory) if f.endswith(".csv"))


@pytest.mark.parametrize("mode", ["strict", "synthetic"])
def test_streaming_output_is_byte_identical_to_eager(tmp_path, mode):
    cau, cc = _source(tmp_path)
    ref = tmp_path / "ref"
    write_result(convert_to_focus_1_4(read_csv_rows(cau), read_csv_rows(cc), mode=mode), ref)
    streamed = tmp_path / "streamed"
    convert_files(str(cau), str(streamed), contract_commitment=str(cc), mode=mode)

    assert _csv_files(ref) == _csv_files(streamed)
    for name in _csv_files(ref):
        assert (ref / name).read_bytes() == (streamed / name).read_bytes(), name
    ref_manifest = json.loads((ref / "focus_1_4_manifest.json").read_text())
    streamed_manifest = json.loads((streamed / "focus_1_4_manifest.json").read_text())
    assert ref_manifest == streamed_manifest
    # The scratch aggregation DB must never be published, and SHA256SUMS must cover every data
    # file — byte-identical to the eager path (regression: streaming once shipped an empty one).
    assert not (streamed / "_index.sqlite").exists()
    assert (streamed / "SHA256SUMS").read_bytes() == (ref / "SHA256SUMS").read_bytes()


def test_streaming_accepts_gzip_input(tmp_path):
    cau, cc = _source(tmp_path)
    gz = tmp_path / "cau.csv.gz"
    gz.write_bytes(gzip.compress(cau.read_bytes()))
    plain = tmp_path / "plain"
    convert_files(str(cau), str(plain), contract_commitment=str(cc), mode="synthetic")
    from_gz = tmp_path / "gz"
    convert_files(str(gz), str(from_gz), contract_commitment=str(cc), mode="synthetic")
    for name in _csv_files(plain):
        assert (plain / name).read_bytes() == (from_gz / name).read_bytes()


def test_streaming_is_deterministic(tmp_path):
    cau, cc = _source(tmp_path)
    a = tmp_path / "a"
    convert_files(str(cau), str(a), contract_commitment=str(cc), mode="synthetic")
    b = tmp_path / "b"
    convert_files(str(cau), str(b), contract_commitment=str(cc), mode="synthetic")
    for name in _csv_files(a):
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_header_only_input_is_rejected(tmp_path):
    # A valid Cost and Usage header with zero data rows must be refused (parity with the eager
    # API), not published as a manifest-only directory.
    module = get_generator("aws", "1.3")
    header = module.generate_csv_bytes(1, 1302).decode().splitlines()[0]
    src = tmp_path / "header_only.csv"
    src.write_text(header + "\n")
    out = tmp_path / "out"
    with pytest.raises(ConversionError, match="no Cost and Usage rows to convert"):
        convert_files(str(src), str(out), mode="synthetic")
    assert not out.exists()
    assert not list(tmp_path.glob(".output.tmp-*"))  # staging cleaned up


def test_error_mid_stream_leaves_no_output(tmp_path):
    cau, _ = _source(tmp_path, n=60)
    lines = cau.read_text().splitlines()
    lines[30] = lines[30] + ",UNEXPECTED_EXTRA_FIELD"  # wrong field count mid-stream
    bad = tmp_path / "bad.csv"
    bad.write_text("\n".join(lines) + "\n")
    out = tmp_path / "out"
    with pytest.raises(MalformedRecordError):
        convert_files(str(bad), str(out), mode="synthetic")
    assert not out.exists()  # nothing published
    assert not list(tmp_path.glob(".output.tmp-*"))  # staging cleaned up


def test_streaming_invoice_detail_reconciles(tmp_path):
    cau, cc = _source(tmp_path)
    out = tmp_path / "o"
    convert_files(str(cau), str(out), contract_commitment=str(cc), mode="synthetic")
    cau_rows = read_csv_rows(cau)
    total_cu = sum(
        (Decimal(r["BilledCost"] or "0") for r in cau_rows if (r.get("InvoiceId") or "").strip()),
        Decimal(0),
    )
    detail = read_csv_rows(out / "synthetic_focus_1_4_invoice_detail.csv")
    total_inv = sum((Decimal(r["BilledCost"]) for r in detail), Decimal(0))
    assert abs(total_inv - total_cu) < Decimal("0.01")


def test_streaming_on_client_like_fixture():
    fixture = Path(__file__).parent / "fixtures" / "client_like" / "consolidated_multi_provider_1_3.csv"
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        ref = Path(d) / "ref"
        write_result(convert_to_focus_1_4(read_csv_rows(fixture), mode="synthetic"), ref)
        streamed = Path(d) / "streamed"
        convert_files(str(fixture), str(streamed), mode="synthetic")
        assert _csv_files(ref) == _csv_files(streamed)
        for name in _csv_files(ref):
            assert (ref / name).read_bytes() == (streamed / name).read_bytes()


@pytest.mark.slow
def test_memory_stays_bounded_at_scale(tmp_path):
    module = get_generator("aws", "1.3")
    peaks: dict[int, float] = {}
    for n in (100_000, 300_000):
        src = tmp_path / f"c{n}.csv"
        src.write_bytes(module.generate_csv_bytes(n, 1302))
        tracemalloc.start()
        convert_files(str(src), str(tmp_path / f"o{n}"), mode="synthetic")
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks[n] = peak
    # Peak Python memory must not scale with row count: 3x the rows must not ~3x the memory.
    # (Measured: a flat ~38 MB regardless of n — the CU file is read once and all aggregation
    # is staged in SQLite, so only one row + one group accumulator + the page cache are live.)
    # Known blind spot: tracemalloc only counts Python-level allocations — native memory held
    # by SQLite (page cache) or PyArrow buffers is invisible to it. Kept deliberately: an
    # RSS-based bound would cover native memory but is runner-noise-flaky, while this check is
    # deterministic and still catches the realistic regression (Python-side row accumulation).
    assert peaks[300_000] < 200e6
    assert peaks[300_000] < peaks[100_000] * 1.5
