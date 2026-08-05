# Runner (Docker / OCI)

The **Runner** is the toolkit packaged as an OCI container for production, automation and large
volumes. Its entrypoint is the `focus-toolkit` CLI, so a container run is exactly a CLI run — the
same manifests, diagnostics, checksums and exit codes, with **no FOCUS logic duplicated**. It is
**batch-only**: there is no HTTP server; status is conveyed by the exit code, the logs, the
`focus_1_4_manifest.json` and the produced files.

Image: `ghcr.io/guymano/focus-data-toolkit`. Tags: **immutable** `<version>` (e.g. `0.11.0`) and
`sha-<full-commit>`; plus a **rolling** `<major>.<minor>` alias (e.g. `0.11`) that advances with
each patch. Pin to `<version>` or a digest for reproducibility; use `<major>.<minor>` to follow
patches. No `latest` tag is published.

New to the Runner? **Before you start** through **Troubleshooting** is a walkthrough you can follow
end to end. Already running it? The reference sections begin at [Layout](#layout).

> **Windows readers:** the bounded-memory streaming engine is **not supported on Windows hosts**
> ([compatibility.md](compatibility.md)). Inside the image it runs on Linux, so the Runner is how
> you get `--stream`, Parquet and partitioned output from a Windows machine.

## Before you start

You need a container runtime — Docker Engine (Linux) or Docker Desktop (Windows/macOS); check with
`docker --version`. Podman works too, with [one pitfall](#podman). Budget ~1 GB of disk for the
image plus room for your data. A FOCUS 1.2/1.3 Cost and Usage export is useful but not required:
step 1 generates a sample.

You do **not** need Python, `pip` or a virtual environment — that is the point of the Runner. The
container makes no network calls, needs no credentials and sends no telemetry: it reads the files
you mount and writes the files you ask for.

## Get the image and prove it works

```bash
docker pull ghcr.io/guymano/focus-data-toolkit:0.11.0
```

Now run something that touches nothing on your machine — no mounts, no volumes. If this fails, the
problem is your Docker installation, not your data or your arguments.

```console
$ docker run --rm ghcr.io/guymano/focus-data-toolkit:0.11.0 version
focus-data-toolkit 0.11.0
  python 3.12.x
  parquet: available
```

`parquet: available` confirms the image ships the `[parquet]` extra, so Parquet output and the
streaming path are usable. Running the image with **no arguments** prints the full command list.
`--rm` deletes the stopped container afterwards — use it everywhere except when you need to inspect
the container after it exits.

## Set up your directories

The image expects three mount points. Create the matching directories on your host:

```bash
mkdir -p focus-demo/input focus-demo/output focus-demo/work && cd focus-demo
```

| Mount | What it is | How to mount it |
|---|---|---|
| `/input` | Your source files. The container never writes here. | read-only (`:ro`) |
| `/output` | Where results are published: datasets, manifest, checksums. | writable |
| `/work` | Scratch: SQLite aggregation index + bundle-validation spill. | writable, fast disk |

The container runs as a **non-root** user (uid 65532), which is where most first-run failures come
from. The rule is short:

- **Bind mounts** (`-v "$PWD/output:/output"`) keep the host directory's ownership, so add
  `--user "$(id -u):$(id -g)"` and mount over **both** `/work` and `/output` — the in-image copies
  are owned by 65532 and would be unwritable to another uid.
- **Named volumes** (`-v fdt-work:/work`) are initialised writable for the image's own user, so use
  them **without** `--user`.

The walkthrough uses bind mounts with `--user`, so you can open the results in your file manager.

> **Windows (PowerShell), read this once:** every `bash` block below translates mechanically — write
> `${PWD}` instead of `$PWD`, use a backtick `` ` `` instead of `\` to continue a line, read the exit
> code from `$LASTEXITCODE` instead of `$?`, and **drop `--user`** (Docker Desktop already maps
> ownership for you):
> ```powershell
> docker run --rm `
>   -v "${PWD}/input:/input:ro" -v "${PWD}/output:/output" -v "${PWD}/work:/work" `
>   ghcr.io/guymano/focus-data-toolkit:0.11.0 `
>   convert --cost-and-usage /input/gen/focus_1_3_cost_and_usage_aws.csv --out /output/result
> ```

## Walkthrough

Six steps, from an empty directory to a validated FOCUS 1.4 output. Run them in order.

### 1. Generate a sample source

No FOCUS export to hand? Generate one. This is the only step that mounts `/input` **writable**.

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/input:/input" \
  ghcr.io/guymano/focus-data-toolkit:0.11.0 \
  generate --provider aws --focus-version 1.3 --rows 1000 --out /input/gen
```

```console
wrote /input/gen/focus_1_3_cost_and_usage_aws.csv (1000 rows, seed 1202)
wrote /input/gen/focus_1_3_contract_commitment_aws.csv
```

Filenames follow `focus_<version>_<dataset>_<provider>.csv`. `--provider` is `aws`, `azure` or
`gcp`; `--focus-version` is `1.2` or `1.3` (Contract Commitment is emitted for 1.3 only).
Generation is deterministic — the same `--seed` gives the same bytes — and happens **in memory**,
so very large row counts need RAM rather than the streaming path. Conversion is what streams.

### 2. Detect what the file is

Before converting anything, confirm the container can see your file. `detect` writes nothing, so
this is a zero-risk probe of your `:ro` mount:

```console
$ docker run --rm -v "$PWD/input:/input:ro" ghcr.io/guymano/focus-data-toolkit:0.11.0 \
    detect /input/gen/focus_1_3_cost_and_usage_aws.csv
/input/gen/focus_1_3_cost_and_usage_aws.csv: dataset=Cost and Usage version=1.3 confidence=HIGH (score 1.000)
```

`confidence=HIGH` with a score of 1.000 means the columns match a known FOCUS release exactly. Add
`--format json` for the machine-readable record — same facts plus `exact_match`, `missing_columns`,
`unknown_columns` and `extension_columns`, which are worth reading whenever confidence is lower.

### 3. Convert to FOCUS 1.4

This is the step that matters. Mount all three directories:

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/input:/input:ro" -v "$PWD/output:/output" -v "$PWD/work:/work" \
  ghcr.io/guymano/focus-data-toolkit:0.11.0 \
  convert --cost-and-usage /input/gen/focus_1_3_cost_and_usage_aws.csv --out /output/result
echo $?
```

```console
source detected: FOCUS 1.3 (dataset Cost and Usage, confidence HIGH, mode: strict)
wrote /output/result/focus_1_4_cost_and_usage.csv
wrote /output/result/focus_1_4_manifest.json
not produced [Contract Commitment]: no source dataset available for this FOCUS 1.4 dataset
not produced [Billing Period]: Mandatory provider-issued fields unavailable from Cost and Usage
not produced [Invoice Detail]: Mandatory provider-issued fields unavailable from Cost and Usage
lint [Cost and Usage]: lint OK
3
```

> **Exit code 3 is the expected result here, not a failure.** Nothing was written to stderr and no
> error was raised — the conversion succeeded. Code 3 says the **strict** default refused to invent
> the three datasets a Cost and Usage source cannot supply; see [supplements.md](supplements.md) to
> fill them from client data. To make an orchestrator treat this as success, add
> `--exit-policy pipeline`, which maps 3 and 4 to **0** while leaving genuine failures non-zero.

Two variants worth knowing — same mounts, so only the trailing subcommand differs:

```bash
# Fill the missing datasets with clearly-labelled assumptions (exit 4): filenames gain a
# `synthetic_` prefix, and stderr carries one WARNING line.
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/input:/input:ro" -v "$PWD/output:/output" -v "$PWD/work:/work" \
  ghcr.io/guymano/focus-data-toolkit:0.11.0 \
  convert --cost-and-usage /input/gen/focus_1_3_cost_and_usage_aws.csv \
    --out /output/synthetic --mode synthetic

# Bounded-memory streaming to partitioned Parquet — the large-file path.
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/input:/input:ro" -v "$PWD/output:/output" -v "$PWD/work:/work" \
  ghcr.io/guymano/focus-data-toolkit:0.11.0 \
  convert --cost-and-usage /input/gen/focus_1_3_cost_and_usage_aws.csv \
    --out /output/parquet --stream --output-format parquet \
    --partition-by BillingCurrency --compression zstd --progress
```

The two paths report differently. The eager CSV conversion lists every file it wrote; the streaming
path prints a single `wrote` line naming the **directory** — followed, in strict mode, by the same
`not produced` line per missing dataset:

```console
wrote /output/parquet/ (format parquet, mode strict)
not produced [Billing Period]: Mandatory provider-issued fields unavailable from Cost and Usage
not produced [Contract Commitment]: no source dataset available for this FOCUS 1.4 dataset
not produced [Invoice Detail]: Mandatory provider-issued fields unavailable from Cost and Usage
```

Per-phase progress and manifest notes go to **stderr**, so stdout stays parseable. With
`--partition-by`, the Cost and Usage output is a **directory** of Parquet parts, not a file. An
existing `--out` is refused (exit 2) unless you pass `--on-exists replace` or `--on-exists version`.

### 4. Look at what you got

```console
$ ls output/result
SHA256SUMS  _run.json  focus_1_4_cost_and_usage.csv  focus_1_4_manifest.json
```

`focus_1_4_manifest.json` carries per-column lineage, per-dataset status and conformance — the
*detailed* functional outcome lives there whatever `--exit-policy` did to the exit code.
`SHA256SUMS` covers the datasets and the manifest. `_run.json` is a run sidecar, deliberately
**excluded from `SHA256SUMS`**: it records run-specific facts that would otherwise break the
byte-identity of a deterministic output.

### 5. Validate the result

Lint a single file, then check the datasets against each other as a bundle:

```console
$ docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/output:/output:ro" -v "$PWD/work:/work" \
    ghcr.io/guymano/focus-data-toolkit:0.11.0 validate-bundle --directory /output/result
validated: Cost and Usage
bundle validation: OK (not_executable=1)
NOT_EXECUTABLE FDT-BUNDLE-001: Cost and Usage ContractApplied references cannot be checked: the Contract Commitment dataset is absent from the bundle
```

`NOT_EXECUTABLE` is not a failure: the check could not run because the dataset it compares against
isn't in the bundle. Add `--format json` for a report with an `ok` flag. Swap the subcommand for
`validate /output/result/focus_1_4_cost_and_usage.csv` to lint one file — it reports
`structural+semantic lint OK` and reminds you that this is **structural lint only, not a full FOCUS
1.4 conformance check**.

Two limits here. `validate-bundle` spills large state through `tempfile`, which follows **`TMPDIR`**
— the image presets `TMPDIR=/work`, so mounting `/work` is what keeps that spill on fast disk. Keep
the `--user` flag on this command too: `tempfile` *probes* its candidate directories for
writability, so an unwritable `/work` does not raise — it quietly falls back to the container's own
`/tmp`, and the spill silently leaves the volume you sized for it. And
`validate --official`, which runs the FinOps `focus-validator`, is **not available in this image**:
it ships the `[parquet]` extra only. For an official-validator run, install the package with the
`[validator]` extra on Python 3.12+ ([compatibility.md](compatibility.md)).

### 6. Recover after a crash

The cooperative cancel — SIGTERM or SIGINT unwinding to exit 130 with nothing published — is a
property of the **streaming** path. The eager CSV conversion installs no signal handler, so
`docker stop` there hits Python's default SIGTERM behaviour and terminates the process mid-flight
(exit 143). So leftovers are possible after a hard kill (OOM, `SIGKILL`, node eviction, exit **137**)
**and** after a plain `docker stop` on a non-`--stream` run:

- `.output.tmp-*`, `.trash-*` and `.replace-journal-*.json` in the **parent** of `--out`, because
  that is where the atomic publish stages its work. `clean --out` sweeps these.
- `fdt-<run_id>/` scratch directories under `FOCUS_TOOLKIT_WORK_DIR` (`/work` in the image), left by
  a killed **streaming** run. `clean` takes only `--out` and does **not** reach `/work` — sweep that
  yourself, from the host or with `--entrypoint sh`.

Sweep the output side:

```console
$ docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/output:/output" \
    ghcr.io/guymano/focus-data-toolkit:0.11.0 clean --out /output/result
nothing to clean
```

> **Run `clean` only when nothing is publishing to that location.** It cannot tell a stale staging
> directory from a live one, so a concurrent conversion would lose its work in progress. The same
> caveat applies to sweeping `/work` by hand.

## Podman

> **Scope of this section:** the Runner is built and exercised end to end with **Docker** in CI
> (`.github/workflows/container.yml`); Podman is not tested in this repository. The image is a plain
> OCI image with no runtime-specific behaviour, and the guidance below follows Podman's documented
> rootless user-namespace semantics — a starting point, not a verified recipe.

Under **rootless** Podman, container uid 0 maps to your host user and every other container uid maps
into your `/etc/subuid` range. The image's own user (uid 65532) therefore lands on a subuid that
does not own your host directories, and a bind-mounted `/output` fails with `Permission denied`.
Map your host user onto that uid instead:

```bash
podman run --rm --userns=keep-id:uid=65532,gid=65532 \
  -v "$PWD/input:/input:ro" -v "$PWD/output:/output" -v "$PWD/work:/work" \
  ghcr.io/guymano/focus-data-toolkit:0.11.0 \
  convert --cost-and-usage /input/gen/focus_1_3_cost_and_usage_aws.csv --out /output/result
```

Alternatively use **named volumes** for `/work` and `/output`: Podman adjusts their ownership to the
container's process user on first use, so no `--userns` flag is needed — the closest analogue to
what this project's CI does with Docker.

Two things not to confuse. **`:U`** recursively `chown`s the host directory to match the container
uid: it works, but it modifies your filesystem and walks every inode, so avoid it on large trees.
**`:z` / `:Z`** are **SELinux relabelling** suffixes and do nothing about uid mapping — you may need
both, but they are not substitutes for each other. Finally, `podman stop -t <seconds>` is the
analogue of `docker stop --time`; the SIGTERM → exit 130 contract is a property of the CLI as PID 1,
not of the runtime.

## Running it in a pipeline

All three examples lean on `--exit-policy pipeline`, so a by-design exit 3 or 4 does not read as a
failed job while genuine failures (1, 2, 5, 130) still do.

### GitHub Actions

```yaml
- name: Convert to FOCUS 1.4
  run: |
    mkdir -p "$GITHUB_WORKSPACE/out" "$GITHUB_WORKSPACE/work"
    docker run --rm --user "$(id -u):$(id -g)" --read-only --tmpfs /tmp \
      -v "$GITHUB_WORKSPACE/input:/input:ro" \
      -v "$GITHUB_WORKSPACE/out:/output" \
      -v "$GITHUB_WORKSPACE/work:/work" \
      ghcr.io/guymano/focus-data-toolkit:0.11.0 \
      convert --cost-and-usage /input/cost_and_usage.csv --out /output/focus-1.4 \
        --stream --output-format parquet --compression zstd \
        --exit-policy pipeline --progress
```

`--user` is what makes the workspace writable — a hosted runner's workspace belongs to the runner
user, not to uid 65532. `--progress` streams phase updates to stderr, so they appear live in the job
log. Note that this repository's own CI uses named volumes rather than `--user` with bind mounts, so
the form above is the documented remedy, not a CI-exercised path.

### Kubernetes Job

```yaml
apiVersion: batch/v1
kind: Job
metadata: { name: focus-convert }
spec:
  backoffLimit: 1
  template:
    spec:
      restartPolicy: Never
      terminationGracePeriodSeconds: 120
      securityContext: { runAsNonRoot: true, runAsUser: 65532, fsGroup: 65532 }
      containers:
        - name: focus-toolkit
          image: ghcr.io/guymano/focus-data-toolkit:0.11.0
          args: ["convert", "--cost-and-usage=/input/cost_and_usage.csv",
                 "--out=/output/focus-1.4", "--stream", "--output-format=parquet",
                 "--partition-by=BillingCurrency", "--exit-policy=pipeline"]
          env:
            - { name: FOCUS_TOOLKIT_MIN_OUTPUT_FREE_BYTES, value: "5GB" }
            - { name: FOCUS_TOOLKIT_MIN_WORK_FREE_BYTES,   value: "5GB" }
          securityContext:
            readOnlyRootFilesystem: true
            allowPrivilegeEscalation: false
            capabilities: { drop: ["ALL"] }
          resources:
            requests: { cpu: "1", memory: "1Gi" }
            limits:   { cpu: "2", memory: "2Gi" }
          volumeMounts:
            - { name: input,  mountPath: /input, readOnly: true }
            - { name: output, mountPath: /output }
            - { name: work,   mountPath: /work }
            - { name: tmp,    mountPath: /tmp }
      volumes:
        - { name: input,  persistentVolumeClaim: { claimName: focus-input, readOnly: true } }
        - { name: output, persistentVolumeClaim: { claimName: focus-output } }
        - { name: work,   emptyDir: { sizeLimit: 50Gi } }
        - { name: tmp,    emptyDir: {} }
```

- `runAsUser: 65532` matches the image's own user; `fsGroup: 65532` is what actually makes the PVCs
  writable to it. `runAsGroup` is omitted on purpose — the image's GID is assigned by `useradd` and
  is asserted nowhere in this repository, so check it before pinning it.
- `readOnlyRootFilesystem: true` is safe: only `/work` and `/output` are written. The `/tmp`
  `emptyDir` mirrors the `--tmpfs /tmp` that CI pairs with `--read-only`.
- `emptyDir` for `/work` with a `sizeLimit`, plus the two `MIN_*_FREE_BYTES` variables, turns a disk
  shortfall into a diagnosable exit 5 rather than a raw `OSError` mid-run — but only on the
  streaming path selected by `--stream` (see [Disk budgets](#disk-budgets-streaming-path-only)).
- `restartPolicy: Never` gives each attempt a fresh pod and a fresh `/work`; `OnFailure` would
  restart the container over dirty scratch. `backoffLimit: 1` is deliberate: with
  `--exit-policy pipeline` the functional outcomes are already 0, and of the real failures, exit 2
  (bad arguments) and exit 1 (lint/write failure) never succeed on a retry — only 5 (disk) and 130
  (eviction) are worth one.
- `terminationGracePeriodSeconds: 120` gives the cooperative cancel time to unwind to exit 130 with
  nothing partial published. Too short a grace period turns it into a `SIGKILL` (137) that leaves
  staging behind — see [step 6](#6-recover-after-a-crash).

### cron

```bash
# One physical line per entry; wrapped here for readability.
17 3 * * * /usr/bin/docker run --rm --user 1000:1000
  -v /srv/focus/input:/input:ro -v /srv/focus/output:/output -v /srv/focus/work:/work
  ghcr.io/guymano/focus-data-toolkit:0.11.0
  convert --cost-and-usage /input/cost_and_usage.csv --out /output/nightly
    --stream --exit-policy pipeline --on-exists version >> /var/log/focus-toolkit.log 2>&1
```

Use the absolute `/usr/bin/docker` — cron's `PATH` is minimal. Keep `--on-exists version` (or
`replace`): the default `refuse` makes every run after the first exit 2. `--exit-policy pipeline`
stops cron mailing you about a by-design exit 3, and `--progress` is best omitted since its
rewritten status line is noise in a log file.

Killed runs need two companion sweeps, not one — `clean` only ever looks at `--out`:

```bash
# Weekly: output-side staging left by a killed run.
0 4 * * 0 /usr/bin/docker run --rm --user 1000:1000 -v /srv/focus/output:/output
  ghcr.io/guymano/focus-data-toolkit:0.11.0 clean --out /output/nightly
    >> /var/log/focus-toolkit.log 2>&1

# Weekly: streaming scratch orphaned under the work mount (nothing in the CLI sweeps this).
30 4 * * 0 find /srv/focus/work -maxdepth 1 -name 'fdt-*' -mtime +7 -exec rm -rf {} +
```

Both are safe only when no conversion is running against those paths — hence the quiet hours and
the `-mtime +7` guard.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `FileNotFoundError: … '/input/…'` and a Python traceback (exit 1) | The path is a *container* path, not a host path. Check the `-v` source and the filename: `docker run --rm -v "$PWD/input:/input:ro" --entrypoint sh <image> -c 'ls -R /input'`. |
| `Permission denied` writing to `/output` or `/work` | A bind mount owned by another uid. Add `--user "$(id -u):$(id -g)"` and mount over **both** directories, or switch to named volumes — see [Permissions with host bind mounts](#permissions-with-host-bind-mounts). |
| `Permission denied` under rootless Podman | Rootless uid remapping, a different problem — see [Podman](#podman). |
| Exit code 3 with no error message | Expected on a Cost-and-Usage-only source in strict mode. Add `--exit-policy pipeline`, supply [supplements](supplements.md), or use `--mode synthetic`. |
| `error: destination … already exists (on_exists=refuse)` (exit 2) | Pick `--on-exists replace` or `--on-exists version`, or a fresh `--out`. |
| Exit 137 or 143, with `.output.tmp-*` or `/work/fdt-*` left behind | 137 is a `SIGKILL` (usually OOM); 143 is `docker stop` on the eager path, which installs no signal handler. Raise the memory limit, use `--stream` (which cancels cooperatively to 130), then run `clean --out` and sweep `/work` separately — see [step 6](#6-recover-after-a-crash). |
| `OfficialValidatorNotInstalled` traceback from `validate --official` | The image ships `[parquet]` only. Run the official validator from a `pip install "focus-data-toolkit[validator]"` on Python 3.12+. |
| `error: the Studio web UI needs the [studio] extra` (exit 2) | The Runner is batch-only; the Studio is a separate install — see [studio.md](studio.md). |

## Layout

| Path | Purpose | Mount |
|---|---|---|
| `/input` | Source files (Cost and Usage, optional Contract Commitment, supplements) | read-only (`:ro`) |
| `/output` | Atomic staging + final files (datasets, manifest, `SHA256SUMS`, `_run.json`) | **writable** |
| `/work` | Scratch: SQLite aggregation index + bundle-validation spill | writable (fast disk for big files) |

`FOCUS_TOOLKIT_WORK_DIR=/work` and `TMPDIR=/work` are preset. The image runs as a **non-root**
user (uid 65532) and is compatible with a **read-only root filesystem** — only `/work` and
`/output` are written.

> **Why `/output` must be writable:** the atomic publish stages results in a temp directory
> *next to* `--out` and renames it into place (same filesystem), so the output location itself is
> written, not just `/work`.

A read-only root filesystem is exercised in CI as `--read-only --tmpfs /tmp`. Keep the `/tmp` tmpfs:
`TMPDIR=/work` sends the toolkit's own temporary files to `/work`, but nothing guarantees CPython or
PyArrow never touch `/tmp`, and the tmpfs costs nothing.

### Permissions with host bind mounts

The container runs as uid 65532. A **named volume** (`-v fdt-work:/work`) is initialised writable
by Docker automatically — do **not** combine it with `--user`, since a fresh named volume inherits
the image directory's ownership. A **host bind mount** (`-v "$PWD/output:/output"`) keeps the host
directory's ownership, so either make it writable by that uid (`chmod`/`chown`) or add
`--user "$(id -u):$(id -g)"`. Nothing in the toolkit depends on the uid — it never reads `$HOME` —
but whichever uid you choose must be able to write **both** `/work` and `/output`.

## Environment variables

| Variable | Effect | Preset in the image |
|---|---|---|
| `FOCUS_TOOLKIT_WORK_DIR` | Scratch directory; each run gets its own subdirectory under it. | `/work` |
| `TMPDIR` | Standard-library temp root. `validate-bundle` spills through it. | `/work` |
| `FOCUS_TOOLKIT_MAX_WORK_BYTES` | Cap on scratch bytes (`FDT-IO-006`). | — |
| `FOCUS_TOOLKIT_MIN_WORK_FREE_BYTES` | Refuse/abort below this free space on the work FS (`FDT-IO-006`). | — |
| `FOCUS_TOOLKIT_MIN_OUTPUT_FREE_BYTES` | Same for the output FS (`FDT-IO-005`). | — |
| `FOCUS_TOOLKIT_LOG_LEVEL` | Level of the `focus_data_toolkit` logger. | — |

Sizes accept `512KB`, `128MB`, `2GB` or a plain byte count; a malformed value is ignored rather than
raising. Two bounds worth stating plainly. `FOCUS_TOOLKIT_WORK_DIR` and `TMPDIR` are **not
interchangeable**: the conversion engine uses the former, while `validate-bundle` spills via the
standard library and follows the latter — they coincide inside the image only because the Dockerfile
sets both. And `FOCUS_TOOLKIT_LOG_LEVEL` **changes nothing in the Runner today**: it is applied only
by the streaming conversion path, and the only component that currently emits log records is the
Studio, which this image does not install. It is listed for completeness, not as a verbosity knob.

## Signals & exit codes

`docker stop` sends **SIGTERM** to PID 1 (the CLI runs in exec form, so it *is* PID 1). The
streaming conversion cancels cooperatively: the atomic staging directory is removed, **nothing
partial is published**, and the process exits **130**. Allow a grace period with
`docker stop --time <seconds>`.

| Code | Meaning for `convert` (`detailed`, the default) |
|---|---|
| 0 | success |
| 1 | lint / bundle / write failure |
| 2 | invalid input / arguments |
| 3 | strict mode left some datasets `NOT_PRODUCED` |
| 4 | synthetic mode — assumptions present |
| 5 | disk budget / free-space exhaustion (`FDT-IO-005/006`) |
| 130 | cancelled (SIGINT/SIGTERM) |

This table is the **`convert`** contract. Codes 3, 4 and 5 and the `--exit-policy` flag exist only
there; `validate`, `validate-bundle`, `detect`, `generate` and `supplements validate` return 0, 1 or
2 on their own semantics. An unhandled I/O error — a missing input path being the common case —
surfaces as a Python traceback with exit **1**, not 2.

For orchestrators (Kubernetes, Airflow, Jenkins, AWS Batch) that treat any non-zero code as
failure, add `--exit-policy pipeline`: functional-but-complete outcomes (3, 4) map to **0**;
genuine failures (1/2/5/130) stay non-zero. The detailed functional status is always in the
manifest and the `_run.json` sidecar.

## Disk budgets (streaming path only)

These budgets are read and enforced by the **bounded-memory streaming** conversion — the path
selected by `--stream`, Parquet output or partitioning. The eager default CSV conversion never
consults them and cannot exit 5; a disk-full there surfaces as a write failure (exit 1).

The scratch (`/work`) and output (`--out`) filesystems are budgeted independently:

- `FOCUS_TOOLKIT_WORK_DIR` — scratch directory (default `/work` in the image).
- `FOCUS_TOOLKIT_MAX_WORK_BYTES` — cap on scratch bytes.
- `FOCUS_TOOLKIT_MIN_WORK_FREE_BYTES` — refuse/abort if the work FS free space drops below this.
- `FOCUS_TOOLKIT_MIN_OUTPUT_FREE_BYTES` — same for the output FS.

A best-effort pre-flight (estimate + reserve, with a safety margin) plus periodic in-run checks
fail fast with `FDT-IO-005` (output) / `FDT-IO-006` (work / budget) and exit code 5, instead of a
raw `OSError` mid-run.

## What the image includes — and what it doesn't

The image installs the package with the **`[parquet]` extra only**, pinned through
`constraints/runtime.txt` so a rebuild of the same release tag installs the same bytes. So: the full
CLI, Parquet read/write, partitioning, the streaming engine, the embedded FOCUS 1.4 model and all
supplement adapters are present. The `[validator]` extra is **not**, so `validate --official` fails
inside the container; nor is `[studio]`, so `ui` exits 2 — the Runner is batch-only *because* the
Studio is not installed, not because of any structural property of the image. Never present at all:
network calls, telemetry, credentials, or any server listening on a port.

## Scale — single-node engine

| Volume | Recommended method |
|---|---|
| Tests / ordinary files | CLI or the local [Studio](studio.md) |
| Large files on one machine | Runner |
| Hundreds of GB | Runner with fast local `/work` + sized CPU/RAM/disk; Parquet + partitioning + `--compression zstd` |
| Beyond a single node | Partition upstream, or orchestrate multiple batches |

Streaming keeps memory bounded, but the Runner is **not** a distributed engine.

## Supply chain

Each published image is scanned with trivy, which fails the release on HIGH/CRITICAL vulnerabilities
**that have a fix available** (`ignore-unfixed: true`); it is **signed with cosign** (keyless OIDC)
and **attested** with GitHub build provenance. The base image is pinned by digest.

The release workflow also generates a CycloneDX SBOM of the image, but — unlike the signature and
the attestation — it is retained as a **workflow artifact** only: it is neither attached to the
GitHub Release nor pushed to the registry, so it is not something you can pull alongside the digest.
(The *package* SBOMs published with the PyPI release are attached to the GitHub Release; that is a
separate artifact.) Verify the signature and the provenance, for example:

```bash
cosign verify ghcr.io/guymano/focus-data-toolkit:0.11.0 \
  --certificate-identity-regexp '^https://github.com/guymano/focus-data-toolkit' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

gh attestation verify oci://ghcr.io/guymano/focus-data-toolkit:0.11.0 --repo guymano/focus-data-toolkit
```

## Operational prerequisites (owner-only)

Publishing (the `release-container.yml` workflow, on a `v*` tag) needs, like the PyPI release:
a GitHub **Environment** named `ghcr` with required reviewers, and **GHCR package write**
permission for the repository. These are configured by a repository admin — see
[releasing.md](releasing.md).
