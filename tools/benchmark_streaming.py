"""Reproducible streaming-conversion benchmark: throughput and peak memory.

Usage::

    python tools/benchmark_streaming.py --rows 100000 500000 --provider aws

For each row count it generates a FOCUS 1.3 Cost and Usage file (excluded from the timing),
then measures the synthetic streaming conversion (``convert_files``): wall-clock throughput
(``time.perf_counter``) and peak process RSS (``resource.getrusage``).

Both the sample generation and the conversion run in **fresh subprocesses**, and the parent
only orchestrates. This matters for the memory figure: ``subprocess`` forks the child (copy-on-
write) from the parent, so if the parent held the multi-hundred-MB generation buffer, the
child's ``ru_maxrss`` would inherit that peak and the conversion would look far heavier than it
is. Generating in a separate process keeps the parent lean, so the conversion worker's peak RSS
reflects the conversion alone — and it stays roughly flat as the row count grows, because the
Cost and Usage file is read once and all aggregation is staged on disk in SQLite.

Deterministic and side-effect-free: it writes only into a throwaway temp directory.
"""

from __future__ import annotations

import argparse
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from focus_data_toolkit.convert import convert_files
from focus_data_toolkit.generators import get_generator

_PEAK_PREFIX = "PEAK_RSS_BYTES "


def _peak_rss_bytes() -> int:
    # ru_maxrss is kilobytes on Linux, bytes on macOS.
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return maxrss * 1024 if sys.platform != "darwin" else maxrss


def _gen_worker(provider: str, version: str, rows: int, seed: int, dest: str) -> int:
    """Child entry point: generate a sample Cost and Usage file (keeps the parent lean)."""
    module = get_generator(provider, version)
    Path(dest).write_bytes(module.generate_csv_bytes(rows, seed))
    return 0


def _convert_worker(src: str, out: str) -> int:
    """Child entry point: convert one file, then report this process's peak RSS."""
    convert_files(src, out, mode="synthetic")
    print(f"{_PEAK_PREFIX}{_peak_rss_bytes()}")
    return 0


def _spawn(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, __file__, *args], capture_output=True, text=True, check=True
    )


def benchmark(rows: list[int], provider: str, seed: int) -> list[dict]:
    results = []
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        for n in rows:
            src = d / f"cau_{n}.csv"
            _spawn("--gen-worker", provider, "1.3", str(n), str(seed), str(src))  # separate proc
            source_bytes = src.stat().st_size

            start = time.perf_counter()
            proc = _spawn("--convert-worker", str(src), str(d / f"out_{n}"))
            elapsed = time.perf_counter() - start
            peak = next(
                (int(line[len(_PEAK_PREFIX):]) for line in proc.stdout.splitlines()
                 if line.startswith(_PEAK_PREFIX)),
                0,
            )
            results.append(
                {
                    "rows": n,
                    "seconds": elapsed,
                    "rows_per_sec": n / elapsed if elapsed else 0.0,
                    "source_mb": source_bytes / 1e6,
                    "peak_rss_mb": peak / 1e6,
                }
            )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Streaming conversion benchmark")
    parser.add_argument("--convert-worker", nargs=2, metavar=("SRC", "OUT"), help=argparse.SUPPRESS)
    parser.add_argument(
        "--gen-worker",
        nargs=5,
        metavar=("PROVIDER", "VERSION", "ROWS", "SEED", "DEST"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--rows", type=int, nargs="+", default=[100_000, 500_000])
    parser.add_argument("--provider", default="aws", choices=("aws", "azure", "gcp"))
    parser.add_argument("--seed", type=int, default=1302)
    args = parser.parse_args(argv)

    if args.convert_worker:
        return _convert_worker(*args.convert_worker)
    if args.gen_worker:
        provider, version, rows, seed, dest = args.gen_worker
        return _gen_worker(provider, version, int(rows), int(seed), dest)

    print(f"{'rows':>10}  {'source MB':>10}  {'seconds':>9}  {'rows/sec':>10}  {'peak RSS MB':>12}")
    for r in benchmark(args.rows, args.provider, args.seed):
        print(
            f"{r['rows']:>10}  {r['source_mb']:>10.1f}  {r['seconds']:>9.2f}  "
            f"{r['rows_per_sec']:>10.0f}  {r['peak_rss_mb']:>12.1f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
