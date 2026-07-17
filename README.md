# focus-data-toolkit

Generate provider-realistic **FOCUS 1.2 / 1.3** sample data (AWS, Azure, GCP),
**migrate** a FOCUS 1.2/1.3 Cost and Usage table to **FOCUS 1.4**, optionally
**synthesize** the new 1.4 datasets for demos/tests, and **lint** the results —
in one dependency-free Python toolkit.

[FOCUS](https://focus.finops.org) (FinOps Open Cost and Usage Specification) is
the open standard for cloud cost and usage data. FOCUS 1.4 (June 2026) defines
four datasets: **Cost and Usage** (65 columns), **Contract Commitment**
(30 columns), **Billing Period** (6 columns, new) and **Invoice Detail**
(22 columns, new).

## What this toolkit does — and does not — claim

The toolkit is explicit about three very different operations:

- **Schema migration** — transforming columns/formats where the mapping is exact
  or explicitly documented. *Cost and Usage* migrates 1.2/1.3 → 1.4 essentially
  losslessly (1.4 only adds nullable columns and drops two deprecated ones).
- **Enrichment** — adding data from a complementary *authoritative* source
  (e.g. a real invoice). Not yet ingested; the architecture reserves a place for it.
- **Synthetic projection** — inventing plausible values for demos/tests/learning.
  These results are **never** presented as real financial facts or as fully
  FOCUS-conformant.

The three new-in-1.4 datasets (**Billing Period**, **Invoice Detail**, and the
1.4-expanded **Contract Commitment**) contain **Mandatory columns that are
provider billing-system facts** — invoice status, payment terms, issuer-assigned
ids, provider record timestamps, commitment commercial terms. These are **not
present in, and not derivable from, a Cost and Usage table**. FOCUS 1.4 itself
treats them as provider-emitted (it adds an *Invoice Reconciliation* feature and
a *Rounding Variance Tolerance* appendix precisely because the issued invoice
legitimately differs from summed usage). So an aggregation of `BilledCost` is a
useful analytical summary — **not** a real invoice line.

A structurally valid file is **not** automatically FOCUS-conformant.

## Modes

| Mode | Behaviour |
|---|---|
| **`strict`** (default) | Never invents provider facts. A canonical FOCUS 1.4 dataset is produced only when every Mandatory non-nullable column has a factual lineage (observed / renamed / derived / enriched). From a Cost-and-Usage source that means **only Cost and Usage** is produced; the other three are reported `NOT_PRODUCED` in the manifest, with the exact blocking columns. |
| **`synthetic`** | Generates assumed values so you get all four datasets for demos/tests. Affected datasets are written with a `synthetic_` filename prefix and marked `PRODUCED_SYNTHETIC` in the manifest; the result is never labelled fully conformant. |

Every conversion writes a deterministic **manifest** (`focus_1_4_manifest.json`)
recording, per column, how each value was obtained
(`OBSERVED` / `RENAMED` / `DERIVED` / `ENRICHED` / `ASSUMED` / `UNAVAILABLE`).

The core package is **standard-library only** (Python ≥ 3.11).

## Quickstart

```bash
pip install focus-data-toolkit
```

The core package is standard-library only and supports **Python 3.11+**. Optional extras add
capabilities: `parquet` (PyArrow columnar output), `validator` (the official FinOps validator,
**Python 3.12+**), and `all` (both). Until the first PyPI release, install from source with
`pip install "git+https://github.com/guymano/focus-data-toolkit"`.

### 1. Generate FOCUS 1.2/1.3 sample data

```bash
focus-toolkit generate --provider aws --focus-version 1.3 --rows 1000 --seed 1302 --out ./out
# -> out/focus_1_3_cost_and_usage_aws.csv (65 columns)
# -> out/focus_1_3_contract_commitment_aws.csv (13 columns)
```

Providers: `aws`, `azure`, `gcp`. Versions: `1.2`, `1.3`. Same rows+seed →
byte-identical output.

### 2. Convert towards FOCUS 1.4

**Strict (default)** — migrate what is genuinely migratable:

```bash
focus-toolkit convert \
  --cost-and-usage out/focus_1_3_cost_and_usage_aws.csv \
  --out ./focus-1.4
# -> focus-1.4/focus_1_4_cost_and_usage.csv   (65 columns)
# -> focus-1.4/focus_1_4_manifest.json        (why the other 3 were NOT produced)
# exit code 3: strict result is intentionally incomplete
```

**Synthetic** — also generate the provider-emitted datasets for demos/tests:

```bash
focus-toolkit convert \
  --cost-and-usage out/focus_1_3_cost_and_usage_aws.csv \
  --contract-commitment out/focus_1_3_contract_commitment_aws.csv \
  --out ./focus-1.4 --mode synthetic
# -> synthetic_focus_1_4_cost_and_usage.csv        (migration + assumed InvoiceDetailId back-link)
# -> synthetic_focus_1_4_contract_commitment.csv   (assumed terms)
# -> synthetic_focus_1_4_billing_period.csv        (assumed status/timestamps)
# -> synthetic_focus_1_4_invoice_detail.csv        (assumed invoice facts)
# -> focus_1_4_manifest.json
# exit code 4: synthetic result contains ASSUMED values
```

In synthetic mode Cost and Usage is also `synthetic_`-prefixed, because its
`InvoiceDetailId` back-links to the (synthetic) Invoice Detail — every other
column is a faithful migration, as the manifest's per-column lineage records. In
strict mode Cost and Usage is emitted unprefixed with its `InvoiceDetailId` left null.

Works the same on your **own** FOCUS 1.2/1.3 exports — the source version is
detected from the CSV header. CLI exit codes: `0` success without assumptions ·
`1` lint violation · `2` invalid arguments · `3` incomplete strict result ·
`4` synthetic result produced with assumptions.

### 3. Lint

```bash
# built-in FOCUS 1.4 structural + semantic linter (not a full conformance validator)
focus-toolkit validate focus-1.4/focus_1_4_cost_and_usage.csv --dataset cost-and-usage

# official FinOps validator (optional extra, requires Python >= 3.12; supports 1.2/1.3)
pip install "focus-data-toolkit[validator]"
focus-toolkit validate --official --focus-version 1.2.0.1 my_focus_1_2_export.csv
```

The official FinOps validator ships rule models for FOCUS 1.2/1.3 and does **not**
yet support 1.4. Until it does, the built-in linter provides a structural +
semantic check only — it cannot certify full FOCUS 1.4 conformance.

### Working with real client data (0.3.0 / P1)

For consolidated, multi-provider, multi-issuer, multi-currency exports:

- **Schema detection** identifies the dataset *and* version (1.2/1.3/1.4) with a confidence,
  and reports missing / extension (`x_`) / unknown columns. Strict mode refuses an ambiguous
  source; force it with `--source-version` / `--source-dataset`.
- **Grouping keys** use the full billing grain (issuer, invoice, account, currency, period,
  charge category), so lines from different issuers/accounts/currencies are never merged.
  Locally generated ids are `x_fdt_…`-prefixed and never presented as issuer-assigned.
- **Cross-dataset validation** (`validate_dataset_bundle`) checks referential integrity,
  currency/period/issuer coherence, invoice reconciliation (with a rounding tolerance),
  Split Cost Allocation, and correction/lifecycle integrity — separately from the
  per-dataset linter.
- **Atomic writes**: output appears only after validation succeeds and checksums + manifest
  are written; nothing partial is left on error. `--on-exists refuse|replace|version`.

```python
from focus_data_toolkit import convert_to_focus_1_4, detect_focus_schema, validate_bundle

detection = detect_focus_schema(headers)          # dataset, version, confidence, ...
report = validate_bundle({"Cost and Usage": cu_rows, "Invoice Detail": invd_rows})
print(report.ok, report.counts())                 # cross-dataset findings, JSON-serialisable
```

### Scale: streaming conversion and Parquet (0.3.0 / P1 Phase B)

For large client files, `convert_files` streams the Cost and Usage file **once** and stages
Invoice Detail aggregation / Billing Period dedup in a throwaway SQLite database, so peak
memory stays **flat regardless of row count** — a constant ~64 MB peak process RSS whether
converting 50k or 300k rows (6× the rows, ×1.05 the memory; `tools/benchmark_streaming.py`).
Its output is **byte-identical** to the in-memory path — both call the same pure
per-row/per-group functions and the same manifest assembler.

```bash
# bounded-memory streaming to CSV (recommended for large exports)
focus-toolkit convert --cost-and-usage huge_cost_and_usage.csv.gz --out ./focus-1.4 \
  --mode synthetic --stream

# Parquet output with exact decimal128 (requires the [parquet] extra)
focus-toolkit convert --cost-and-usage huge_cost_and_usage.csv --out ./focus-1.4 \
  --mode synthetic --output-format parquet
```

Gzip input is auto-detected. Exactness contract: **CSV is byte-exact** (the literal), while
**Parquet is decimal-value-exact** — decimal columns are written as `decimal128(precision,
scale)` (never binary float; a value needing more scale than the column allows raises with its
line number instead of rounding silently), dates as UTC timestamps, JSON/strings verbatim.

**Partitioning & compression** (Parquet): `--partition-by` writes the Cost and Usage dataset as
a Hive-partitioned tree on low-cardinality String / Date-Time columns; `--compression`
(snappy default / zstd / gzip / none) and `--target-file-size` (roll to a new part file per
partition) tune the layout:

```bash
focus-toolkit convert --cost-and-usage huge_cost_and_usage.csv --out ./focus-1.4 \
  --mode synthetic --output-format parquet \
  --partition-by BillingCurrency,BillingPeriodStart --compression zstd --target-file-size 128MB
# -> synthetic_focus_1_4_cost_and_usage/BillingCurrency=USD/BillingPeriodStart=.../part-0.parquet
```

The partition columns are stored in the paths (standard Hive), so any `pyarrow.dataset` reader
reconstructs full rows; a too-high-cardinality key is warned about and, past a hard cap,
refused (nothing partial is published).

```python
from focus_data_toolkit import convert_files

out = convert_files("huge_cost_and_usage.csv.gz", "./focus-1.4",
                    mode="synthetic", output_format="parquet",
                    partition_by=["BillingCurrency"])  # -> published Path
```

`pip install "focus-data-toolkit[parquet]"` for Parquet; streaming CSV needs no extra
(the external state uses the standard-library `sqlite3`).

### Synthetic scenarios (SCA, corrections, billing lifecycle)

Deterministic, provider-agnostic scenario builders produce self-consistent data for tests and
demos, and typed lifecycle checks validate status transitions:

```python
from focus_data_toolkit.generators.scenarios import (
    split_allocation_group, correction_set, billing_lifecycle_instances,
)
from focus_data_toolkit import check_status_transitions, validate_bundle

alloc = split_allocation_group("origin-1", "100.00", weights=[3, 2, 1])   # ratios sum to 1,
validate_bundle({"Cost and Usage": alloc}).ok                             # costs sum to origin

corr = correction_set("chg-1", "100.00", ["-30.00"])   # original + signed Correction line,
validate_bundle({"Cost and Usage": corr}).ok           # running net recorded in x_NetCharge

check_status_transitions(billing_lifecycle_instances())  # [] — only allowed transitions
```

### Python API

```python
from focus_data_toolkit import convert_to_focus_1_4, lint_focus_1_4_structure
from focus_data_toolkit.convert import read_csv_rows
from focus_data_toolkit.modes import Mode

result = convert_to_focus_1_4(read_csv_rows("focus_1_3_cost_and_usage.csv"))  # strict
print(result.coverage)               # ('Cost and Usage',)
print(result.not_produced)           # ('Contract Commitment', 'Billing Period', 'Invoice Detail')
print(result.manifest["datasets"]["Invoice Detail"]["blocking_columns"])

result = convert_to_focus_1_4(read_csv_rows("focus_1_3_cost_and_usage.csv"), mode=Mode.SYNTHETIC)
rows = result.datasets["Invoice Detail"]
print(lint_focus_1_4_structure("Invoice Detail", rows).levels_passed)
```

## What is really migratable

| 1.4 dataset | Real migration? | Notes |
|---|---|---|
| Cost and Usage | **Yes** | Column mapping (1.2 lifted to 1.3 shape; deprecated provider columns dropped; nullable 1.4 additions null; `PricingCurrency*` backfilled; `ContractApplied` re-cased 1.3→1.4). |
| Contract Commitment | Partial → **synthetic** | The 13 source columns migrate; the 14 Mandatory 1.4 commercial terms are provider facts → assumed (synthetic only). |
| Billing Period | **No** (synthetic) | Period keys derive from Cost and Usage; `BillingPeriodStatus` and the timestamps are provider billing-cycle state. |
| Invoice Detail | **No** (synthetic) | `BilledCost` aggregates usage, but invoice status/terms/issuer-assigned id/timestamps are provider-issued. |

Conversion is pure (no clock, no RNG): the same input always produces the same
output bytes, in both modes.

## Development

```bash
git clone https://github.com/guymano/focus-data-toolkit && cd focus-data-toolkit
pip install -e .[dev]           # includes pyarrow, so the Parquet suite runs (not skipped)
pytest -q                       # generators, detection, migration/lint, modes, manifest, CLI,
                                # streaming, Parquet, split-allocation, corrections, lifecycle
pytest -m slow                  # large-scale bounded-memory test (excluded by default)
python tools/benchmark_streaming.py --rows 100000 500000   # throughput + peak RSS
ruff check src tests
```

The FOCUS 1.4 model JSON (`src/focus_data_toolkit/model/focus_1_4_model.json`)
is the artifact of record, extracted from the FinOps Foundation "FOCUS 1.4
Data Model" workbook with `tools/extract_focus_1_4_model.py` (the workbook
itself is not redistributed here — download it from
[focus.finops.org](https://focus.finops.org)).

## License and credits

MIT. FOCUS is a trademark of the FinOps Foundation; the FOCUS specification,
data model workbook and official validator are © the FinOps Foundation — this
project is an independent community toolkit and is not endorsed by the FinOps
Foundation. Related sample datasets from the same generators were contributed
to [FOCUS-Sample-Data](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS-Sample-Data)
(PRs #6 and #7).
