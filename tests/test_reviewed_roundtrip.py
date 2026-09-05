"""New financial precision survives 1.4 migration, aggregation and columnar I/O."""
from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal as D

import pytest

from focus_data_toolkit.convert import convert_files, convert_to_focus_1_4
from focus_data_toolkit.generators import get_generator
from focus_data_toolkit.generators.engine.serialize import generate_bundle_csv_bytes
from focus_data_toolkit.modes import Mode


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
@pytest.mark.parametrize("version", ["1.2", "1.3"])
def test_exact_migration_and_parquet(tmp_path, provider, version):
    pytest.importorskip("pyarrow")
    from focus_data_toolkit.io.parquet_io import ParquetRowReader, ParquetRowWriter
    from focus_data_toolkit.io.records import DatasetSchema

    m = get_generator(provider, version)
    rows = m.generate_rows(1000, include_credits=True)
    cc = m.generate_contract_commitment_rows(1000, include_credits=True) if version == "1.3" else None
    result = convert_to_focus_1_4(rows, cc, mode=Mode.SYNTHETIC)
    assert all(r.ok for r in result.reports.values())
    for before, after in zip(rows, result.datasets["Cost and Usage"], strict=True):
        for key in ("BilledCost", "EffectiveCost", "ListCost", "ContractedCost", "PricingCurrencyEffectiveCost"):
            assert D(before[key]) == D(after[key])
        for key in ("SkuId", "SkuPriceId", "ResourceId"):
            assert before[key] == after[key]
        if before.get("ContractApplied"):
            assert json.loads(before["ContractApplied"]) == json.loads(after["ContractApplied"])
    totals: dict[str, D] = defaultdict(D)
    for row in result.datasets["Cost and Usage"]:
        totals[row["InvoiceDetailId"]] += D(row["BilledCost"])
    for row in result.datasets["Invoice Detail"]:
        assert D(row["BilledCost"]) == totals[row["InvoiceDetailId"]]
    for dataset, data in result.datasets.items():
        if not data:
            continue
        path = tmp_path / (dataset + ".parquet")
        with ParquetRowWriter(path, DatasetSchema(dataset, tuple(data[0]))) as writer:
            for row in data:
                writer.write(row)
        restored = [r.values for r in ParquetRowReader(path, dataset=dataset)]
        for src, dst in zip(data, restored, strict=True):
            for key, value in src.items():
                if "Cost" in key and value:
                    assert D(value) == D(dst[key])


def test_cli_bundle_matches_individual_apis():
    for provider in ("aws", "azure", "gcp"):
        m = get_generator(provider, "1.3")
        cau, cc = generate_bundle_csv_bytes(1000, 42, profile=m.PROFILE, adapter=m.ADAPTER)
        assert cau == m.generate_csv_bytes(1000, 42)
        assert cc == m.generate_contract_commitment_csv_bytes(1000, 42)


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="streaming supported on POSIX")
def test_streaming_partitioned_precision(tmp_path):
    pytest.importorskip("pyarrow")
    from focus_data_toolkit.io.parquet_io import PartitionedParquetReader

    m = get_generator("aws", "1.3")
    source = tmp_path / "cau.csv"
    source.write_bytes(m.generate_csv_bytes(1000))
    eager = convert_to_focus_1_4(m.generate_rows(1000), mode=Mode.SYNTHETIC)
    convert_files(source, tmp_path / "out", mode="synthetic",
                  output_format="parquet", partition_by=["BillingCurrency", "ChargeCategory"])
    directory = tmp_path / "out" / "synthetic_focus_1_4_cost_and_usage"
    restored = [r.values for r in PartitionedParquetReader(
        directory, "Cost and Usage", ["BillingCurrency", "ChargeCategory"])]
    assert len(restored) == 1000
    assert sum(D(r["BilledCost"]) for r in restored) == sum(D(r["BilledCost"]) for r in eager.datasets["Cost and Usage"])
