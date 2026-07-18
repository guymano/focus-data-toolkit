# focus-data-toolkit

Generate realistic **FOCUS 1.2 / 1.3** cost & usage data (AWS, Azure, GCP), convert it to
**FOCUS 1.4**, and validate the result — from a single dependency-free Python core, usable three ways.

[![PyPI](https://img.shields.io/pypi/v/focus-data-toolkit)](https://pypi.org/project/focus-data-toolkit/)
[![Python](https://img.shields.io/pypi/pyversions/focus-data-toolkit)](https://pypi.org/project/focus-data-toolkit/)
[![License: MIT AND CC-BY-4.0](https://img.shields.io/badge/license-MIT%20AND%20CC--BY--4.0-blue)](LICENSE)
[![CI](https://github.com/guymano/focus-data-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/guymano/focus-data-toolkit/actions/workflows/ci.yml)

[FOCUS](https://focus.finops.org) is the open standard for cloud cost & usage data. FOCUS 1.4 defines
four datasets: **Cost and Usage**, **Contract Commitment**, **Billing Period** and **Invoice Detail**.
This toolkit migrates real data, synthesizes sample data, and checks structure — honestly: a
structurally valid file is not automatically FOCUS-conformant, and the toolkit never invents facts it
cannot derive from the source.

## Three ways to use it

| Interface | Best for | Get started |
|---|---|---|
| **Studio** | FinOps users who prefer a UI | `pip install "focus-data-toolkit[studio]"` → `focus-toolkit ui` |
| **Runner** | Automation & large volumes | `docker run --rm ghcr.io/guymano/focus-data-toolkit:0.11.0 version` |
| **CLI & SDK** | Engineers & scripts | `pip install focus-data-toolkit` → `focus-toolkit --help` |

All three drive the **same core**, so their outputs are byte-for-byte identical — same datasets,
manifest, diagnostics and `SHA256SUMS`. No FOCUS logic is duplicated across interfaces.

- **Studio** — a local, single-user web app: pick or upload a file (or generate one), detect it,
  convert with live progress, preview a sampled page, and download the results. Binds `127.0.0.1`,
  token-guarded; no data leaves your machine. → [docs/studio.md](docs/studio.md)
- **Runner** — a non-root OCI image whose entrypoint *is* the CLI, for batch jobs and CI.
  → [docs/runner.md](docs/runner.md)
- **CLI & SDK** — the `focus-toolkit` command and the importable Python API.

## Install

```bash
pip install focus-data-toolkit
```

The core is **standard-library only** (Python ≥ 3.11). Optional features live behind extras:

| Extra | Adds |
|---|---|
| `parquet` | Parquet I/O — columnar, decimal128, Hive partitioning (alias: `scale`) |
| `studio` | the local web UI (`focus-toolkit ui`) |
| `studio-all` | `studio` + `parquet` |
| `validator` | the official FinOps validator (`--official`; needs Python ≥ 3.12) |
| `all` | `parquet` + `validator` + `studio` |

```bash
pipx install focus-data-toolkit                        # isolated CLI
uv tool install focus-data-toolkit                     # or with uv
docker pull ghcr.io/guymano/focus-data-toolkit:0.11.0  # container (Runner)
```

## Quickstart

```bash
# 1. Generate provider-realistic sample data (aws|azure|gcp, FOCUS 1.2|1.3)
focus-toolkit generate --provider aws --focus-version 1.3 --rows 1000 --out ./out

# 2. Convert a 1.2/1.3 Cost & Usage file to FOCUS 1.4 (source version auto-detected)
focus-toolkit convert --cost-and-usage out/focus_1_3_cost_and_usage_aws.csv --out ./focus-1.4
#    add --mode synthetic  to also emit the other three datasets (clearly labelled synthetic)
#    add --stream --output-format parquet  for large files

# 3. Open the Studio web app
focus-toolkit ui
```

Use it as a library — the same engine the CLI, Studio and Runner run:

```python
from focus_data_toolkit import convert_files

convert_files("cost_and_usage.csv", "focus-1.4", mode="strict")
```

## What it does — and what it doesn't

- **Converts** FOCUS **1.2 / 1.3 Cost & Usage → the four FOCUS 1.4 datasets.** `strict` mode emits
  only what is genuinely derivable from the source; `synthetic` mode also fills the
  provider-billing datasets with clearly-labelled assumed data for demos and tests.
- **Honest by design.** The source is Cost & Usage only, so Contract Commitment / Billing Period /
  Invoice Detail come **only** from client [supplements](docs/supplements.md) or synthetic mode.
  There is **no FOCUS 1.4 generator** and **no invalid-data generator**. Generation is in-memory
  (use the CLI/Runner for very large synthetic sets), and the engine is **single-node**.
- **Built for real data.** Bounded-memory streaming conversion, atomic/journaled writes, a
  deterministic manifest with per-column lineage, structured `FDT-*` diagnostics, and a
  cross-dataset validation gate.

See [docs/compatibility.md](docs/compatibility.md) for the Python / OS / FOCUS support matrix.

## Exit codes

`convert` returns meaningful codes so pipelines can branch on the outcome:

| Code | Meaning |
|---|---|
| `0` | success, no assumptions |
| `1` | lint / validation / write failure |
| `2` | invalid input or arguments |
| `3` | strict result intentionally incomplete |
| `4` | synthetic result contains assumptions |
| `5` | disk space / budget exhausted |
| `130` | cancelled (Ctrl-C / SIGTERM) — nothing partial is published |

Add `--exit-policy pipeline` to treat the functional-but-incomplete outcomes (`3`, `4`) as `0`.

## Trust & provenance

- **Deterministic:** the same input always produces the same output bytes (no clock, no RNG); every
  artifact is listed in `SHA256SUMS`.
- **Verifiable model:** the embedded FOCUS 1.4 model is extracted from the FinOps source workbook and
  its provenance is **complete** — hash-pinned and reproduced byte-for-byte.
  See [docs/model-provenance.md](docs/model-provenance.md).
- **Supply chain:** releases are built reproducibly, signed (Sigstore/cosign), and ship an SBOM and
  build attestations. See [docs/releasing.md](docs/releasing.md).
- **Private:** the core does **no network I/O**, has **no telemetry**, and needs **no credentials**.
  See [docs/security-model.md](docs/security-model.md).

## Documentation

[Studio](docs/studio.md) · [Runner](docs/runner.md) · [Supplements & gaps](docs/supplements.md) ·
[Compatibility](docs/compatibility.md) · [Versioning](docs/versioning.md) ·
[Security model](docs/security-model.md) · [Model provenance](docs/model-provenance.md) ·
[Releasing](docs/releasing.md) · [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) ·
[Security policy](SECURITY.md)

## License & credits

Code is **MIT** (see [LICENSE](LICENSE)). The embedded FOCUS 1.4 data model is a derivative of the
FinOps FOCUS data-model workbook, © the FinOps Foundation and licensed **CC-BY-4.0**, redistributed
with attribution (see [NOTICE](NOTICE)). "FOCUS" and "FinOps" are trademarks of the FinOps
Foundation; this is an independent community project, not endorsed by the FinOps Foundation. Related
sample datasets were contributed upstream to
[FOCUS-Sample-Data](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS-Sample-Data).
