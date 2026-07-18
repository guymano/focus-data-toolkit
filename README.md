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

## Installation

### Prerequisites

- **Python 3.11, 3.12 or 3.13** (the `validator` extra needs **3.12+**). Linux, macOS and
  Windows are supported — CI tests Ubuntu and Windows across all three versions (see
  [docs/compatibility.md](docs/compatibility.md)).
- `pip` (or [uv](https://docs.astral.sh/uv/)). **No compiler is needed**: the core package is
  pure Python and standard-library only — zero runtime dependencies.

### Standard install

In a virtual environment (recommended):

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install focus-data-toolkit
```

> **Until the first PyPI release**, install straight from GitHub instead:
> `pip install "git+https://github.com/guymano/focus-data-toolkit"`
> (append `@vX.Y.Z` to pin a tag once releases exist).

This installs the `focus-toolkit` command (alias of `python -m focus_data_toolkit`).

### Optional extras

The core covers generation, 1.2/1.3 → 1.4 conversion, client supplements + the AWS/Azure/GCP
provider-export adapters, bounded-memory CSV streaming, and every validation gate — all with
the standard library alone (the streaming state uses built-in `sqlite3`). Extras add:

| Command | Adds | Requires |
|---|---|---|
| `pip install "focus-data-toolkit[parquet]"` | Parquet **input and output** (PyArrow; value-exact decimal128, Hive partitioning). On Windows, `tzdata` comes along automatically. | Python ≥ 3.11 |
| `pip install "focus-data-toolkit[validator]"` | the official FinOps `focus-validator`, used by `focus-toolkit validate --official` (rule models for FOCUS 1.2/1.3) | Python ≥ 3.12 |
| `pip install "focus-data-toolkit[all]"` | both of the above (on 3.11 the validator part is skipped automatically by its Python marker) | Python ≥ 3.11 |

### With uv

```bash
uv tool install focus-data-toolkit                     # standalone CLI on your PATH
uv add "focus-data-toolkit[parquet]"                   # as a project dependency
uvx --from focus-data-toolkit focus-toolkit --help     # one-off run, nothing installed
# (--from is needed because the command name differs from the package name)
```

### From source

```bash
git clone https://github.com/guymano/focus-data-toolkit
cd focus-data-toolkit
pip install .                                # or: pip install ".[all]"
```

For a development install (editable, with the test/lint toolchain), see
[Development](#development). Release artifacts (wheel, sdist) attached to GitHub Releases
ship with `SHA256SUMS`, two SBOM profiles and GitHub Artifact Attestations — verify a
downloaded wheel and install it directly:

```bash
# SHA256SUMS covers every release asset; check just the wheel's line:
grep "focus_data_toolkit-X.Y.Z-py3-none-any.whl" SHA256SUMS | sha256sum -c -
gh attestation verify focus_data_toolkit-X.Y.Z-py3-none-any.whl --repo guymano/focus-data-toolkit
pip install ./focus_data_toolkit-X.Y.Z-py3-none-any.whl
```

(To verify the whole asset set at once, download all files listed in `SHA256SUMS` into one
directory and run `sha256sum -c SHA256SUMS` there.)

### Verify the installation

```bash
focus-toolkit --help                                         # CLI is on the PATH
# venv/project installs only — a `uv tool install` lives in its own isolated
# environment, so check it via the tool env instead:
python -c "import focus_data_toolkit as f; print(f.__version__)"
#   uv tool run --from focus-data-toolkit python -c "import focus_data_toolkit as f; print(f.__version__)"
# end-to-end smoke test (deterministic, ~1 s):
focus-toolkit generate --provider aws --focus-version 1.3 --rows 10 --out /tmp/fdt-smoke
focus-toolkit convert --cost-and-usage /tmp/fdt-smoke/focus_1_3_cost_and_usage_aws.csv \
  --out /tmp/fdt-smoke/focus-1.4
# exit code 3 is expected: the strict result is intentionally incomplete without supplements
```

### Upgrade / uninstall

```bash
pip install -U focus-data-toolkit
pip uninstall focus-data-toolkit
```

## Quickstart

Install first (see [Installation](#installation) above), then:

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
  per-dataset linter. It runs as a **publication gate** on every conversion (eager and
  streaming): an ERROR refuses to publish, the outcome is recorded in the manifest's
  `bundle_validation` section, and `--no-validate` skips it (the skip is recorded too).
  In the streaming path the checks re-read the staged files in bounded memory, spilling
  per-key lookup state to a scratch SQLite database past a threshold.
- **Atomic writes**: output appears only after validation succeeds and checksums + manifest
  are written; nothing partial is left on error. `--on-exists refuse|replace|version`.
  A `replace` swap is **journaled**: if the process dies between its two renames, the next
  publish to the same destination (or `focus-toolkit clean --out DIR`) reads the journal and
  rolls the fully staged result forward — or the previous result back — so the destination
  is never left missing. `clean` also removes orphan `.output.tmp-*` / `.trash-*` leftovers.

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

Sources may be **CSV (gzip ok) or Parquet** — the format is sniffed per file (magic bytes,
extension as fallback), so `--cost-and-usage export.parquet` works everywhere a CSV does
(`convert`, `gaps`, `supplements validate`), in both the eager and streaming paths.

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
demos, and typed lifecycle checks validate the full snapshot chains — status transitions plus
chain structure (id/order uniqueness, `previous_instance_id` resolution, cycle detection,
`last_updated` monotonicity, closed-instance immutability; `FDT-LIFE-001..006`):

```python
from focus_data_toolkit.generators.scenarios import (
    split_allocation_group, correction_set, billing_lifecycle_instances,
)
from focus_data_toolkit import check_dataset_instances, validate_bundle

alloc = split_allocation_group("origin-1", "100.00", weights=[3, 2, 1])   # ratios sum to 1,
validate_bundle({"Cost and Usage": alloc}).ok                             # costs sum to origin

corr = correction_set("chg-1", "100.00", ["-30.00"])   # original + signed Correction line,
validate_bundle({"Cost and Usage": corr}).ok           # running net recorded in x_NetCharge

check_dataset_instances(billing_lifecycle_instances())  # [] — chains + transitions all valid
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

## Runner (Docker / OCI)

For production, automation and large volumes, the toolkit ships as a container image whose
entrypoint **is** the `focus-toolkit` CLI — a container run is exactly a CLI run (same
manifests, diagnostics, checksums and exit codes; no FOCUS logic is duplicated). It is
**batch-only** (no HTTP server); status is the exit code, the logs, and the produced files.

```bash
docker run --rm \
  --read-only --tmpfs /tmp \
  -v "$PWD/input:/input:ro" \
  -v "$PWD/output:/output" \
  -v fdt-work:/work \
  ghcr.io/guymano/focus-data-toolkit:0.10.0 \
  convert --cost-and-usage /input/focus.csv \
    --stream --output-format parquet --out /output/result \
    --exit-policy pipeline
```

- **Non-root** (uid 65532) and **read-only-rootfs compatible** — only `/work` (scratch) and
  `/output` are written. Because the atomic publish stages next to `--out`, **`/output` must be
  writable** (mount a writable volume/dir; if you run as the image's non-root uid, ensure the
  target is writable by it — or use a named volume, which Docker initialises writable for you).
- `/input` is meant to be mounted **read-only**. `FOCUS_TOOLKIT_WORK_DIR=/work` and `TMPDIR=/work`
  are preset, so all scratch stays on the `/work` volume — point it at fast local storage for big
  files, and size it (the SQLite aggregation + bundle spill scale with invoice/period/commitment
  cardinalities, not with the Cost-and-Usage row count).
- **Signals**: `docker stop` sends SIGTERM to PID 1 (the CLI), which cancels cleanly — exit code
  **130**, nothing partial published. Give it a grace period (`--stop-timeout`).
- **Exit codes** for orchestrators: use `--exit-policy pipeline` so a functional-but-incomplete
  strict run (3) or a synthetic run (4) reports success (0); genuine failures (1/2/5/130) stay
  non-zero. Disk budgets: `FOCUS_TOOLKIT_MAX_WORK_BYTES`, `FOCUS_TOOLKIT_MIN_WORK_FREE_BYTES`,
  `FOCUS_TOOLKIT_MIN_OUTPUT_FREE_BYTES` (a shortfall fails fast with `FDT-IO-005/006`, exit 5).

**Single-node engine — pick the right method by scale:**

| Volume | Recommended method |
|---|---|
| Tests / ordinary files | CLI (or the forthcoming Studio) |
| Large files on one machine | Runner |
| Hundreds of GB | Runner with fast local `/work` + sized resources; Parquet + partitioning + zstd |
| Beyond a single node | Partition upstream / orchestrate multiple batches |

The image is not a distributed engine. **Immutable** tags — `<version>` (e.g. `0.10.0`) and
`sha-<full-commit>` — always identify the same bytes; the `<major>.<minor>` tag (e.g. `0.10`)
is a **rolling** convenience alias that advances with each patch release. Every release is
scanned (trivy) **before** its public tags are assigned, carries a CycloneDX SBOM, and is signed
(cosign) and attested (build provenance). See [docs/runner.md](docs/runner.md) for details.

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
[focus.finops.org](https://focus.finops.org)). Its machine-readable provenance
is recorded in `model_provenance.json` and can be checked with
`python scripts/verify_model_provenance.py`; see
[docs/model-provenance.md](docs/model-provenance.md).

## Contributing, security & docs

- **Contributing:** [CONTRIBUTING.md](CONTRIBUTING.md)
- **Security policy / reporting:** [SECURITY.md](SECURITY.md) (private reporting;
  a FOCUS conformance bug is a normal issue, not a security report)
- **Versioning & reproducibility:** [docs/versioning.md](docs/versioning.md)
- **Compatibility (Python / OS / FOCUS):** [docs/compatibility.md](docs/compatibility.md)
- **Security model:** [docs/security-model.md](docs/security-model.md)
- **Releasing:** [docs/releasing.md](docs/releasing.md)
- **Changelog:** [CHANGELOG.md](CHANGELOG.md)

## License and credits

The toolkit code is **MIT** (see [LICENSE](LICENSE)). The embedded FOCUS 1.4 data
model is a derivative of the FinOps FOCUS specification / data-model workbook,
which is © the FinOps Foundation and licensed **CC-BY-4.0**; it is redistributed
here with attribution (see [NOTICE](NOTICE) and
[docs/model-provenance.md](docs/model-provenance.md)). "FOCUS" and "FinOps" are
trademarks of the FinOps Foundation; the FOCUS specification, data-model workbook
and official validator are © the FinOps Foundation — this project is an
independent community toolkit and is not endorsed by the FinOps Foundation.
Related sample datasets from the same generators were contributed to
[FOCUS-Sample-Data](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS-Sample-Data)
(PRs #6 and #7).
