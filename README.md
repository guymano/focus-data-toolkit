# focus-data-toolkit

Generate provider-realistic **FOCUS 1.2 / 1.3** sample data (AWS, Azure, GCP),
**convert** FOCUS 1.2/1.3 data to the four **FOCUS 1.4** datasets, and
**validate** the results — in one dependency-free Python toolkit.

[FOCUS](https://focus.finops.org) (FinOps Open Cost and Usage Specification) is
the open standard for cloud cost and usage data. FOCUS 1.4 (June 2026) defines
four datasets: **Cost and Usage** (65 columns), **Contract Commitment**
(30 columns), **Billing Period** (6 columns, new) and **Invoice Detail**
(22 columns, new). No cloud provider exports the full 1.4 set yet — this
toolkit builds it from the 1.2/1.3 data you already have.

## Features

- **Generate** — deterministic, synthetic, PII-free FOCUS 1.2 (57-column) and
  1.3 (65-column Cost and Usage + 13-column Contract Commitment) CSVs with
  provider-realistic values: real service names, SKUs, regions, pricing units,
  commitment models (Savings Plans / Reservations / CUDs), tax and credit
  rows. A given `(rows, seed)` pair is byte-reproducible.
- **Convert** — reshape a FOCUS 1.2/1.3 Cost and Usage table (and optional 1.3
  Contract Commitment table) into the four FOCUS 1.4 datasets:
  - version auto-detected from the header;
  - deprecated `ProviderName`/`PublisherName` dropped, 1.4 columns added;
  - Contract Commitment expanded from 13 to 30 columns;
  - Billing Period and Invoice Detail **derived** from the Cost and Usage rows
    (`sum(BilledCost)` reconciles by construction; every Cost and Usage row
    back-links to its `InvoiceDetailId`);
  - honest coverage: datasets that cannot be derived from your source are
    reported as missing, never fabricated.
- **Validate** — a built-in, model-driven FOCUS 1.4 conformance validator
  (structural, format and cross-field rules from the committed FOCUS 1.4 data
  model), plus a wrapper for the official
  [FinOps FOCUS validator](https://github.com/finopsfoundation/focus_validator).

The core package is **standard-library only** (Python ≥ 3.11).

## Quickstart

```bash
pip install git+https://github.com/guymano/focus-data-toolkit
```

### 1. Generate FOCUS 1.2/1.3 sample data

```bash
focus-toolkit generate --provider aws --focus-version 1.3 --rows 1000 --seed 1302 --out ./out
# -> out/focus_1_3_cost_and_usage_aws.csv (65 columns)
# -> out/focus_1_3_contract_commitment_aws.csv (13 columns)
```

Providers: `aws`, `azure`, `gcp`. Versions: `1.2`, `1.3`. Same rows+seed →
byte-identical output.

### 2. Convert FOCUS 1.2/1.3 to FOCUS 1.4

```bash
focus-toolkit convert \
  --cost-and-usage out/focus_1_3_cost_and_usage_aws.csv \
  --contract-commitment out/focus_1_3_contract_commitment_aws.csv \
  --out ./focus-1.4
# -> focus-1.4/focus_1_4_cost_and_usage.csv        (65 columns)
# -> focus-1.4/focus_1_4_contract_commitment.csv   (30 columns)
# -> focus-1.4/focus_1_4_billing_period.csv        (6 columns, derived)
# -> focus-1.4/focus_1_4_invoice_detail.csv        (22 columns, derived)
```

Works the same on your **own** FOCUS 1.2/1.3 exports — the source version is
detected from the CSV header. Every produced dataset is validated against the
FOCUS 1.4 model before the command succeeds (skip with `--no-validate`).

From a 1.2 source (which has no Contract Commitment dataset), coverage is
declared partial: the three derivable datasets are produced, Contract
Commitment is not invented.

### 3. Validate

```bash
# built-in FOCUS 1.4 model validator
focus-toolkit validate focus-1.4/focus_1_4_invoice_detail.csv --dataset invoice-detail

# official FinOps validator (optional extra, requires Python >= 3.12)
pip install "focus-data-toolkit[validator] @ git+https://github.com/guymano/focus-data-toolkit"
focus-toolkit validate --official --focus-version 1.2.0.1 my_focus_1_2_export.csv
```

The official validator ships rule models for FOCUS 1.2/1.3; FinOps has
announced 1.4 rule-model support for later in 2026 — until then the built-in
model validator is the 1.4 conformance gate.

### Python API

```python
from focus_data_toolkit import convert_to_focus_1_4, validate_focus_1_4
from focus_data_toolkit.convert import read_csv_rows

result = convert_to_focus_1_4(read_csv_rows("focus_1_3_cost_and_usage.csv"))
print(result.source_version)          # "1.3"
print(result.coverage)                # datasets actually produced
rows = result.datasets["Invoice Detail"]
print(validate_focus_1_4("Invoice Detail", rows).ok)
```

## How the 1.4 derivations work

| 1.4 dataset | Source | Method |
|---|---|---|
| Cost and Usage | 1.2/1.3 Cost and Usage | Column mapping (1.2 is first lifted to the 1.3 shape; deprecated provider columns dropped; nullable 1.4 additions null; non-nullable `PricingCurrency*` backfilled from billing currency when the source left them null) |
| Contract Commitment | 1.3 Contract Commitment | 13→30 expansion: derivable columns from the source (period, currency, cost), deterministic documented defaults for the rest (see `convert/contract_commitment.py`) |
| Billing Period | derived | One row per distinct `(BillingPeriodStart, BillingPeriodEnd, InvoiceIssuerName)` |
| Invoice Detail | derived | One row per `(InvoiceId, ChargeCategory)`; `BilledCost` is the exact Decimal sum of the matching Cost and Usage rows; `InvoiceDetailId` is a deterministic hash back-linked from Cost and Usage |

Conversion is pure (no clock, no RNG): the same input always produces the same
output bytes.

## Development

```bash
git clone https://github.com/guymano/focus-data-toolkit && cd focus-data-toolkit
pip install -e .[dev]
pytest -q          # 37 tests: generators, detection, round-trip conformance, CLI
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
