# Generator corrections prepared for 0.13.0

This is an adapted port of [FOCUS-Sample-Data PR 6](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS-Sample-Data/pull/6)
at `e14921a3ba8d5f75a7b899d878e372999482ba9a` and
[PR 7](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS-Sample-Data/pull/7)
at `99917010b27f7c0ca3586f8e0daab7e1c8103dac`. These are examined commits,
not a dependency on the eventual merged PRs. The previous toolkit baseline is
`d0c27bac6d1909fa39fe45538fce3a9d391ae0f8` (0.12.0).

## Corrected behavior

| Area | Previous output | New output |
|---|---|---|
| Period subscription | Effective cost zero, losing the fee in amortized views | Effective cost equals billed cost; pricing-currency amount follows the same exact exchange rate |
| Tax | Independent random amount | Exactly 10% of each corresponding source amount on an earlier, previously untaxed Standard Usage record |
| SKU | Random identifiers and list-price jitter | Stable canonical offer identity and stable public price; a separate subscription offer |
| Storage | Generator registry omitted recognized storage properties; legacy redundancy values | One property registry; AWS Standard/Zonal and Azure Hot/Local storage values |
| AWS resource | Payer account and inferred namespace | Owning subaccount and explicit service namespace, also for allocated resources |
| Commitment | Two to four machine-hours per period | Fleet capacity 500 machine-hours per hourly period, partially used |
| Group budget | Builder could overflow; final slicing could hide it | Every builder respects its budget; empty groups fall back to ordinary usage; exact final row count |
| Ordinary 1.3 contract | Every ordinary Standard Usage row negotiated | Deterministic approximately 35% selection; 10% discount for selected rows, public price otherwise and on ordinary allocations |
| Official result | Set of FAIL rules; an empty report could pass | Full rule inventory, states, violations, totals, process result, pinned resources and reproducible evidence |
| Invoice aggregate | Rounded to six decimals | Exact sum, including after FOCUS 1.4 conversion and Parquet serialization |

The archived pre-change examples are in
[`pr67_before.json`](../tests/fixtures/golden/correctness_migration/pr67_before.json).
The corresponding corrected outputs are the regenerated provider/version CSVs in
[`compatibility_golden`](../tests/fixtures/golden/compatibility_golden/).
The independent audit in `scripts/sample_audit.py` checks semantics rather than
reusing generator builders. Deliberately corrupted subscriptions, taxes, SKUs,
owners, units, storage properties, contract parents and missing purchases must fail.

## Generation assumptions and identity

Taxes are illustrative, not tax advice or jurisdiction-specific billing logic.
The description contains a one-based **data record** reference (excluding the CSV
header). The tax inherits account, service, period, currencies and tags; each
taxable monetary column is multiplied from its own source, exactly once. If there
is no eligible untaxed source, the scheduled tax becomes ordinary usage.

Eligible Standard Usage includes a split-allocation child. In that case the tax
applies to that child's amount, not the total host charge. The source record
reference identifies the exact share. Tax rows do not copy `Allocated*` fields:
they are separate tax charges, not additional members of the source allocation
group. This intentional sample policy is tested explicitly.

Prices are synthetic public and negotiated rates, not current cloud price lists.
Offer identity includes provider, service, meter, region, unit and canonical SKU
properties, excluding seed, account, contract and pricing category. Price identity
also includes the public list prices and billing/pricing currencies. The fixed
illustrative exchange rates remain part of the provider profiles. Quantity and
price precision are bounded before multiplication; cost products are not rounded.

Each commitment period contains a purchase, **two partially used fleet records**,
and unused capacity. Used plus unused capacity is exactly 500; effective costs
sum to the purchase cost. Fleet URNs and `SyntheticFleetSize: 500` tags explicitly
describe synthetic aggregation rather than a single cloud machine. The fleet-size
tag deliberately uses the JSON number 500: it is a numeric synthetic measurement,
not an imitation of a provider's string-valued native tag export. The annual
capacity commitment is 500 × 8,760 = **4,380,000 machine-hours**, with the existing
unit price. Spend commitments retain their cost-based metric. All commitment
periods lie within their annual term, including samples crossing the billing-month
hour boundary.

In 1.3, eligible compute gets an ordinary usage-minimum term, storage a negotiated
rate term, and other services a minimum-spend term. Unselected usage and ordinary
allocations have no negotiated application. Discount commitments share a parent
contract in groups of at most three, with compatible provider/account/currency
identity. Parent dates span their terms. A generation-local registry supplies both
datasets and avoids state leaking between calls. Contract Commitment APIs now
accept keyword-only `include_credits=False`; pass the same rows, seed and options
when generating the two datasets separately.

## ContractApplied compatibility

Generated Spend elements contain applied cost; Usage elements contain applied
quantity and unit. Inapplicable properties are omitted. The parser still reads
older objects with explicit nulls and converts existing 1.3 objects carrying both
metrics without rewriting their meaning.

The 1.3 rule model both describes optional properties and requires five
properties per element. This conflict is documented in the
[examined upstream notes](https://github.com/guymano/FOCUS-Sample-Data/blob/99917010b27f7c0ca3586f8e0daab7e1c8103dac/FOCUS-1.3/validation/upstream-notes.md).
We preserve the toolkit's applicable metric branches. This differs deliberately
from the upstream sample object shape and remains visible in official failures.

## Reproducible official evidence

The reference uses **focus-validator 2.2.1**, models **1.2.0.1 / 1.3.0.1**,
applicability **ALL**, 1,000 rows, default seeds 1202/1302, credits disabled.
Both model hashes and the currency resource hash are checked before execution.
Dataset roots, dependencies and nested rule references independently determine
the expected rule inventory. Empty, duplicate, incomplete, inconsistent or crashed
reports fail. Any state or violation-count change fails the reference comparison,
including PASS becoming SKIPPED with no new FAIL rule.

[`baseline.json`](../tests/fixtures/official/generator_validation/baseline.json)
contains generation-source fingerprints (LF-normalized), exact CSV hashes,
resource versions/hashes, parameters, every rule result and per-failure evidence.
Source hashes remain a strict gate, including version and audit-script changes.
Even a source-only edit requires a reviewed new candidate; dataset hashes cover
the selected fixtures, while source provenance identifies the code used to
produce and interpret the evidence. Mismatches now list the changed files.
Each residual failure has its model expression, explanation, a minimal source
record and independently checked population where the count represents rows.
Composite failures count expressions instead of rows and are labeled separately.
Raw stdout/stderr logs are archived beside it; stdout hashes preserve their exact
original bytes. The nine archived stderr logs are required to be empty, and the
offline check verifies this. Successful live comparisons remove their temporary
files; failed runs retain them for investigation. Candidate directories persist.
Live log paths/timing can differ across operating systems, but
the complete semantic inventory must match.

| Dataset | Provider | PASS | FAIL | SKIPPED |
|---|---|---:|---:|---:|
| Cost and Usage 1.2 | AWS | 353 | 15 | 210 |
| Cost and Usage 1.2 | Azure / GCP, each | 357 | 11 | 210 |
| Cost and Usage 1.3 | AWS | 428 | 39 | 297 |
| Cost and Usage 1.3 | Azure / GCP, each | 432 | 35 | 297 |
| Contract Commitment 1.3 | Each provider | 76 | 0 | 38 |

Residual failures include AWS numeric-ID type inference, conditional/nullability
evaluation and the ContractApplied property-count conflict. These are **reviewed
non-regression results, not a conformance certificate**. Their counters were
reproduced from toolkit output, not copied from upstream PRs.

```bash
# Offline: no official dependency or network required
python scripts/validate_official_samples.py --check-existing
python scripts/describe_generated_samples.py

# Live reference comparison (Python 3.12+, isolated validator environment)
pip install focus-validator==2.2.1
python scripts/validate_official_samples.py

# A separate candidate for review; this never updates the committed reference
python scripts/validate_official_samples.py --record /tmp/new-focus-candidate
```

Promotion requires reviewing the candidate's raw reports, semantic audit and
failure evidence, then explicitly copying its baseline and logs to the fixture
directory. CI only compares; it never records new expectations. The public
`validate --official` command shares the report parser, rejects FAIL even when the
subprocess returns zero, and applies **none** of the sample-only exceptions.
Its output is buffered until completion. It requires console output on stdout
and rejects conflicting output flags with exit code 2. An installed validator
version different from the tested 2.2.1 produces a warning; an incompatible or
incomplete console report fails with an explanatory message.

## Statistics and limits

[`generator-statistics.json`](generator-statistics.json) is generated by
`python scripts/describe_generated_samples.py --write`; the command without
`--write` verifies it. Amounts and ratios use Decimal arithmetic. Commitment share
is purchase cost / total billed cost; utilization and waste use used/unused
effective costs / commitment purchases. Compute coverage is covered list cost /
all eligible compute-usage list cost, excluding unused records. Contract application
counts include discount-backed usage/purchases as well as ordinary negotiated usage.

The six default fixtures have commitment share **18.4–30.9%**, utilization
**69.6–70.5%**, and compute coverage **99.2–99.96%**. The high compute coverage is a
consequence of fleets mixed with small ordinary usage, not a target or an estimate
of real customer behavior. The only pedagogical threshold is commitment share
at least 5% on the default 1,000-row fixtures. Small samples need not contain every
scenario. Closed synthetic periods reconcile billed/effective totals globally and
by account, period and currency; this is not imposed on client datasets.

## Migration and verification

Version 0.13.0 intentionally changes generated bytes and identities. CSV columns
and commands are preserved. Affected Parquet monetary columns use `decimal128(38,17)`;
unit prices retain their existing scales. The wider fractional scale reduces the
maximum integer digits to 21 for those columns. Nonrepresentable values fail
explicitly; no silent rounding is introduced. Pin 0.12.0 to reproduce old bytes.

Verification covers three providers, both source versions, credits on/off, seeds
0/1/42/default and sizes 1–12, 23/24/25, 100/1000/1001. FOCUS 1.4 conversion,
invoice aggregates, CSV/Parquet, partial allocations, interleaved generation calls,
contract references and Linux streaming/partitioned output are exercised. Strict
conversion of external data does not rewrite costs, taxes or SKUs to imitate the
synthetic generator.

### Local verification on 2026-09-05

| Check | Result |
|---|---|
| Complete default suite, Windows / Python 3.12 | 1,101 passed, 25 platform skips; 89.96% coverage |
| Complete default suite, Linux / Python 3.12 | 1,126 passed; 90.21% coverage |
| Targeted generation, golden, report and Parquet suite, Windows / Python 3.11 and 3.13 | 497 passed, 4 platform skips per version |
| Same targeted suite, Linux / Python 3.11 and 3.13 | 501 passed per version |
| Linux packaging and release checks | 13 passed, including wheel/sdist build and clean installation |
| Linux bounded-memory conversion, 100,000 then 300,000 rows | Passed; Python allocation peak stays below 200 MB and grows by less than 1.5x for 3x the rows (tracemalloc excludes native allocations) |
| Live official reference comparison | All nine complete reports match between Windows and Linux |
| Lint, types, action pins, model provenance, offline evidence and documented statistics | Passed |

These are local executions performed before PR creation. The repository's CI
retains its full OS/Python matrix, including the existing macOS smoke run; remote
results are reported on the PR. Publishing remains a separate release step.
