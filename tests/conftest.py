from __future__ import annotations

import csv
import io
import sys

import pytest

from focus_data_toolkit.generators import get_generator

ROWS = 100
SEEDS = {"1.2": 1202, "1.3": 1302}

# --------------------------------------------------------------------------- #
# Known Windows-incompatible tests (skipped on win32, run everywhere else).
#
# The bounded-memory streaming engine (`convert_files`) manages many low-level file handles and
# publishes results via POSIX directory fsync + rename; on Windows this raises `OSError: Bad file
# descriptor` and leaves the staging dir behind. The path-traversal guard also assumes POSIX
# separators. These need a Windows dev environment to fix and are tracked as a "Windows
# streaming / atomic-write hardening" follow-up (documented in docs/compatibility.md, P2-D).
# Everything else — generators, the eager in-memory conversion, validation, schema detection,
# CLI generate/validate, Parquet *write* (with tzdata) — runs on Windows and is validated there.
# --------------------------------------------------------------------------- #
_WINDOWS_SKIP_MODULES = {"test_streaming.py"}
_WINDOWS_SKIP_TESTS = {
    "test_atomic_write.py::test_path_traversal_names_are_rejected",
    "test_cli.py::test_convert_stream_csv_matches_eager",
    "test_cli.py::test_convert_stream_honors_manifest_option",
    "test_cli.py::test_convert_parquet_output",
    "test_parquet.py::test_convert_files_parquet_reconciles_with_csv",
    "test_parquet.py::test_convert_files_parquet_row_count_matches_csv",
    "test_parquet.py::test_metadata_records_target_and_source_version",
    "test_partitioning.py::test_convert_files_partitioned_layout_and_reconciliation",
    "test_partitioning.py::test_partitioned_manifest_and_checksums",
    "test_partitioning.py::test_target_file_size_rolls_part_files",
    "test_partitioning.py::test_compression_codecs_are_accepted",
    "test_partitioning.py::test_high_cardinality_partition_warns",
    "test_partitioning.py::test_cli_partitioned_parquet",
}


def pytest_collection_modifyitems(config, items):
    if sys.platform != "win32":
        return
    skip = pytest.mark.skip(
        reason="known Windows-incompatible (streaming file descriptors / POSIX paths); "
        "tracked Windows-hardening follow-up"
    )
    for item in items:
        rel = item.nodeid.rsplit("/", 1)[-1]  # e.g. "test_streaming.py::test_foo[param]"
        module = rel.split("::", 1)[0]
        test = rel.split("[", 1)[0]  # drop any parametrization id
        if module in _WINDOWS_SKIP_MODULES or test in _WINDOWS_SKIP_TESTS:
            item.add_marker(skip)


def _rows(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


@pytest.fixture(scope="session")
def source_tables():
    """{(provider, version): (cau_rows, cc_rows_or_None)} for the full matrix."""
    tables = {}
    for provider in ("aws", "azure", "gcp"):
        for version in ("1.2", "1.3"):
            module = get_generator(provider, version)
            cau = _rows(module.generate_csv_bytes(ROWS, SEEDS[version]))
            cc = (
                _rows(module.generate_contract_commitment_csv_bytes(ROWS, SEEDS[version]))
                if version == "1.3"
                else None
            )
            tables[(provider, version)] = (cau, cc)
    return tables
