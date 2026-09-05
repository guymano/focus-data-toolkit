# focus-data-toolkit

Generate realistic **FOCUS 1.2 / 1.3** cost and usage data (AWS, Azure, GCP), convert it to
**FOCUS 1.4**, and validate the result. One dependency-free Python core, usable three ways.

[![PyPI](https://img.shields.io/pypi/v/focus-data-toolkit)](https://pypi.org/project/focus-data-toolkit/)
[![Python](https://img.shields.io/pypi/pyversions/focus-data-toolkit)](https://pypi.org/project/focus-data-toolkit/)
[![License: MIT AND CC-BY-4.0](https://img.shields.io/badge/license-MIT%20AND%20CC--BY--4.0-blue)](LICENSE)
[![CI](https://github.com/guymano/focus-data-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/guymano/focus-data-toolkit/actions/workflows/ci.yml)

[FOCUS](https://focus.finops.org) is the open standard for cloud cost and usage data. FOCUS 1.4 defines four datasets: Cost and Usage, Contract Commitment, Billing Period, and Invoice Detail. The toolkit is honest by design: any value it cannot derive from your source is left empty (**strict** mode) or filled with clearly labelled assumptions (**synthetic** mode).

The source prepares **0.13.0** with corrected subscriptions, traceable taxes,
stable offers and partially used fleets of 500 machines. See the
[generator corrections and validation evidence](docs/generator-corrections.md)
for the new byte baseline and sample assumptions. The Docker examples below
continue to pin the previously released 0.12.0 image.

## Three ways to use it

| Interface | Best for | Get started |
|---|---|---|
| **Studio** | FinOps users who prefer a UI | `pip install "focus-data-toolkit[studio]"` then `focus-toolkit ui` |
| **Runner** | Automation and large volumes | `docker run --rm ghcr.io/guymano/focus-data-toolkit:0.12.0 version` |
| **CLI & SDK** | Engineers and scripts | `pip install focus-data-toolkit` then `focus-toolkit --help` |

All three run the same core, so their outputs are byte-for-byte identical. Studio is a local web app (binds `127.0.0.1`, token-guarded, no data leaves your machine, see [docs/studio.md](docs/studio.md)). Runner is a non-root OCI image whose entrypoint is the CLI (see [docs/runner.md](docs/runner.md)).

## Install

```bash
pip install focus-data-toolkit
```

The core needs only the standard library (Python 3.11+). Optional extras:

| Extra | Adds |
|---|---|
| `parquet` | Parquet I/O: columnar, decimal128, Hive partitioning (alias: `scale`) |
| `studio` | the local web UI (`focus-toolkit ui`) |
| `studio-all` | `studio` + `parquet` |
| `validator` | the official FinOps validator (`--official`, needs Python 3.12+) |
| `all` | everything above |

Also available via `pipx install`, `uv tool install`, or `docker pull ghcr.io/guymano/focus-data-toolkit:0.12.0`.

## Quickstart

```bash
# 1. Generate sample data (aws|azure|gcp, FOCUS 1.2|1.3)
focus-toolkit generate --provider aws --focus-version 1.3 --rows 1000 --out ./out

# 2. Convert a 1.2/1.3 Cost and Usage file to FOCUS 1.4 (source version auto-detected)
focus-toolkit convert --cost-and-usage out/focus_1_3_cost_and_usage_aws.csv --out ./focus-1.4

# 3. Open the Studio web app (needs the studio extra)
focus-toolkit ui
```

Or use it as a Python library, the same engine behind all three interfaces:

```python
from focus_data_toolkit import convert_files

convert_files("cost_and_usage.csv", "focus-1.4", mode="strict")
```

## What it does, and what it doesn't

- Converts FOCUS 1.2/1.3 Cost and Usage into the four FOCUS 1.4 datasets.
- The three other datasets come only from [supplements](docs/supplements.md) or synthetic mode.
  There is no FOCUS 1.4 generator and no invalid-data generator.
- Built for real data: bounded-memory streaming (`--stream`), atomic writes, a deterministic
  manifest with per-column lineage, structured `FDT-*` diagnostics, cross-dataset validation.
- Single-node engine. See [docs/compatibility.md](docs/compatibility.md) for the support matrix.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | success, no assumptions |
| `1` | lint / validation / write failure |
| `2` | invalid input or arguments |
| `3` | strict result intentionally incomplete |
| `4` | synthetic result contains assumptions |
| `5` | disk space or budget exhausted (streaming / Runner) |
| `130` | cancelled (Ctrl-C / SIGTERM), nothing partial is published |

Add `--exit-policy pipeline` in `set -e` or CI scripts to treat codes `3` and `4` as `0`.

## Trust

- Deterministic: same input, same output bytes, all listed in `SHA256SUMS`.
- The embedded FOCUS 1.4 model is hash-pinned to the FinOps source workbook; see [docs/model-provenance.md](docs/model-provenance.md).
- Releases are reproducible, signed (Sigstore/cosign), with SBOM and attestations; see [docs/releasing.md](docs/releasing.md).
- Private: no network I/O, no telemetry, no credentials; see [docs/security-model.md](docs/security-model.md).

## Documentation

[Studio](docs/studio.md) · [Runner](docs/runner.md) · [Supplements](docs/supplements.md) · [Compatibility](docs/compatibility.md) · [Versioning](docs/versioning.md) · [Security model](docs/security-model.md) · [Model provenance](docs/model-provenance.md) · [Releasing](docs/releasing.md) · [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Security policy](SECURITY.md)

## License

Code is MIT (see [LICENSE](LICENSE)). The embedded FOCUS 1.4 data model derives from the FinOps FOCUS workbook, © the FinOps Foundation, CC-BY-4.0 (see [NOTICE](NOTICE)). "FOCUS" and "FinOps" are FinOps Foundation trademarks; this is an independent community project, not endorsed by the FinOps Foundation.
