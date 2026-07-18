# Runner (Docker / OCI)

The **Runner** is the toolkit packaged as an OCI container for production, automation and large
volumes. Its entrypoint is the `focus-toolkit` CLI, so a container run is exactly a CLI run — the
same manifests, diagnostics, checksums and exit codes, with **no FOCUS logic duplicated**. It is
**batch-only**: there is no HTTP server; status is conveyed by the exit code, the logs, the
`focus_1_4_manifest.json` and the produced files.

Image: `ghcr.io/guymano/focus-data-toolkit` (tags: `<version>`, `<major>.<minor>`, `sha-<commit>`).

## Layout

| Path | Purpose | Mount |
|---|---|---|
| `/input` | Source files (Cost and Usage, optional Contract Commitment, supplements) | read-only (`:ro`) |
| `/output` | Atomic staging + final files (datasets, manifest, `SHA256SUMS`) | **writable** |
| `/work` | Scratch: SQLite aggregation index + bundle-validation spill | writable (fast disk for big files) |

`FOCUS_TOOLKIT_WORK_DIR=/work` and `TMPDIR=/work` are preset. The image runs as a **non-root**
user (uid 65532) and is compatible with a **read-only root filesystem** — only `/work` and
`/output` are written.

> **Why `/output` must be writable:** the atomic publish stages results in a temp directory
> *next to* `--out` and renames it into place (same filesystem), so the output location itself is
> written, not just `/work`.

## Examples

Streaming conversion to partitioned Parquet, read-only rootfs:

```bash
docker run --rm \
  --read-only --tmpfs /tmp \
  -v "$PWD/input:/input:ro" \
  -v "$PWD/output:/output" \
  -v fdt-work:/work \
  ghcr.io/guymano/focus-data-toolkit:0.10.0 \
  convert --cost-and-usage /input/focus.csv \
    --stream --output-format parquet --partition-by BillingCurrency \
    --out /output/result --exit-policy pipeline
```

Generate synthetic test data (into a writable volume), then validate a bundle:

```bash
docker run --rm -v fdt-work:/work ghcr.io/guymano/focus-data-toolkit:0.10.0 \
  generate --provider aws --focus-version 1.3 --rows 100000 --out /work/gen

docker run --rm -v "$PWD/bundle:/input:ro" ghcr.io/guymano/focus-data-toolkit:0.10.0 \
  validate-bundle --directory /input --format json
```

### Permissions with host bind mounts

The container runs as uid 65532. A **named volume** (`-v fdt-work:/work`) is initialised writable
by Docker automatically. A **host bind mount** (`-v "$PWD/output:/output"`) keeps the host
directory's ownership, so either make it writable by that uid (`chmod`/`chown`) or add
`--user "$(id -u):$(id -g)"` (the CLI works as any uid; only `/work` and `/output` need to be
writable by whatever uid you choose).

## Signals & exit codes

`docker stop` sends **SIGTERM** to PID 1 (the CLI runs in exec form, so it *is* PID 1). The
streaming conversion cancels cooperatively: the atomic staging directory is removed, **nothing
partial is published**, and the process exits **130**. Allow a grace period with
`docker stop --time <seconds>`.

| Code | Meaning (`detailed`, the default) |
|---|---|
| 0 | success |
| 1 | lint / bundle / write failure |
| 2 | invalid input / arguments |
| 3 | strict mode left some datasets `NOT_PRODUCED` |
| 4 | synthetic mode — assumptions present |
| 5 | disk budget / free-space exhaustion (`FDT-IO-005/006`) |
| 130 | cancelled (SIGINT/SIGTERM) |

For orchestrators (Kubernetes, Airflow, Jenkins, AWS Batch) that treat any non-zero code as
failure, add `--exit-policy pipeline`: functional-but-complete outcomes (3, 4) map to **0**;
genuine failures (1/2/5/130) stay non-zero. The detailed functional status is always in the
manifest and the `_run.json` sidecar.

## Disk budgets (two filesystems)

The scratch (`/work`) and output (`--out`) filesystems are budgeted independently:

- `FOCUS_TOOLKIT_WORK_DIR` — scratch directory (default `/work` in the image).
- `FOCUS_TOOLKIT_MAX_WORK_BYTES` — cap on scratch bytes.
- `FOCUS_TOOLKIT_MIN_WORK_FREE_BYTES` — refuse/abort if the work FS free space drops below this.
- `FOCUS_TOOLKIT_MIN_OUTPUT_FREE_BYTES` — same for the output FS.

A best-effort pre-flight (estimate + reserve, with a safety margin) plus periodic in-run checks
fail fast with `FDT-IO-005` (output) / `FDT-IO-006` (work / budget) and exit code 5, instead of a
raw `OSError` mid-run.

## Scale — single-node engine

| Volume | Recommended method |
|---|---|
| Tests / ordinary files | CLI (or the local Studio, once available) |
| Large files on one machine | Runner |
| Hundreds of GB | Runner with fast local `/work` + sized CPU/RAM/disk; Parquet + partitioning + `--compression zstd` |
| Beyond a single node | Partition upstream, or orchestrate multiple batches |

Streaming keeps memory bounded, but the Runner is **not** a distributed engine.

## Supply chain

Each published image is scanned (trivy, fails on HIGH/CRITICAL), carries a CycloneDX SBOM, and is
**signed with cosign** (keyless OIDC) and **attested** with GitHub build provenance. The base
image is pinned by digest. Verify, for example:

```bash
cosign verify ghcr.io/guymano/focus-data-toolkit:0.10.0 \
  --certificate-identity-regexp '^https://github.com/guymano/focus-data-toolkit' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

gh attestation verify oci://ghcr.io/guymano/focus-data-toolkit:0.10.0 --repo guymano/focus-data-toolkit
```

## Operational prerequisites (owner-only)

Publishing (the `release-container.yml` workflow, on a `v*` tag) needs, like the PyPI release:
a GitHub **Environment** named `ghcr` with required reviewers, and **GHCR package write**
permission for the repository. These are configured by a repository admin — see
[releasing.md](releasing.md).
