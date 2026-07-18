# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/) with
[PEP 440](https://peps.python.org/pep-0440/) version strings. See
[docs/versioning.md](docs/versioning.md) for the versioning and reproducibility
policy.

## [Unreleased]

## [0.11.0] — 2026-07-18

First **stable** release. Same feature set as `0.11.0rc1`, promoted to a final release now that the
embedded FOCUS 1.4 model's **provenance is `complete`** — the FinOps Foundation source workbook is
hashed (`source.artifact_sha256`) and the committed model was reproduced from it **byte-for-byte** by
the pinned extractor — so it installs with a plain `pip install focus-data-toolkit` (no `--pre`).

Highlights (full details in the `0.11.0rc1` entry below):

- **Studio** — a local web UI (`focus-toolkit ui`, extra `[studio]`) over the same Core.
- **Runner** — a containerised batch image (GHCR) whose entrypoint is the `focus-toolkit` CLI.
- **Core (Lot A)** — progress/cancellation, separate work/output disk budgets, `--exit-policy`, and
  the `detect` / `validate-bundle` / `version` commands.

### Changed

- **Model provenance**: `partial` → `complete` (source workbook hashed and end-to-end reproduction
  verified; see [docs/model-provenance.md](docs/model-provenance.md)).
- **Release workflow (`release.yml`)**: the GitHub Release step is idempotent — if a release for the
  tag already exists (e.g. created via the "Draft a new release" UI), it updates that release in
  place and attaches the attested assets instead of failing.

## [0.11.0rc1] — 2026-07-18

First release candidate published to PyPI — a **pre-release** (marked as such per the honesty gate,
because the embedded FOCUS model's provenance is `partial`; see
[docs/model-provenance.md](docs/model-provenance.md)). It bundles the three deployment access
methods on the single Core: the CLI/SDK, the containerised **Runner** (Lot B) and the local
**Studio** web UI (Lot C), plus the Core progress/cancellation, disk-budget and CLI additions
(Lot A). Install with `pip install --pre focus-data-toolkit` (pre-releases are not selected by a
plain `pip install`).

### Added — Studio: local web UI (deployment Lot C)

- **`focus-toolkit ui`** launches a local web app (FastAPI, behind the optional `[studio]` extra;
  the command imports it lazily so a core install is unaffected) over the **same Core** — every
  operation drives the same SDK the CLI/Runner use, so its manifests, diagnostics and checksums are
  identical. Detect a source, pick a file under `--root` / upload (capped) / generate synthetic
  data, convert (strict|synthetic, CSV|Parquet) with **live per-phase progress and cancel**,
  preview a **sampled** page (the full file is never loaded), and download datasets, manifest,
  diagnostics (JSON/CSV), `SHA256SUMS` and an HTML summary.
- **Security:** binds `127.0.0.1` by default (a non-loopback `--host` is refused without
  `--allow-remote`); a fresh per-start token is required on every API call; `Host`/`Origin` are
  validated (anti DNS-rebinding / CSRF); file access is confined to the allowlisted `--root`;
  uploads stream to disk and are size-capped. No telemetry, no external upload.
- **Path confinement:** `resolve_within_root` walks real directory entries (matching each component
  by name, never concatenating the user string into a path) **and** canonicalises every matched
  entry with `Path.resolve` — a symlink, Windows junction or reparse point whose real target
  escapes `--root` is refused, while a link that stays inside is followed; absolute, drive-relative
  and UNC paths and `..` traversal are rejected.
- **Bounded by design:** one conversion at a time by default (extra submissions queue); per-job
  scratch under a work dir with TTL + startup cleanup; generation is row-capped in the UI (use the
  CLI/Runner for very large synthetic sets). New extras `studio` and `studio-all`; see
  [docs/studio.md](docs/studio.md).

### Added — Runner: containerised batch image (deployment Lot B)

- **OCI image** (`Dockerfile`) whose entrypoint **is** the `focus-toolkit` CLI — a container run
  equals a CLI run (same manifests, diagnostics, checksums, exit codes; no FOCUS logic
  duplicated). Batch-only (no HTTP server). Multi-stage build on a **digest-pinned**
  `python:3.12-slim-bookworm`, bundling the `[parquet]` extra; **non-root** (uid 65532),
  **read-only-rootfs compatible** (only `/work` and `/output` written), `FOCUS_TOOLKIT_WORK_DIR=/work`.
  Exec-form entrypoint so `docker stop` (SIGTERM) cancels cleanly (exit 130, nothing partial
  published). Volumes: `/input` (ro), `/output`, `/work`. See [docs/runner.md](docs/runner.md).
- **Container CI** (`.github/workflows/container.yml`): builds the image on every PR / push to
  `main` (no publish) and runs `docker run` smoke tests — non-root uid, read-only-rootfs streaming
  Parquet convert, exit codes, SIGTERM handling — plus a trivy scan (fails on HIGH/CRITICAL). A
  fast static test (`tests/test_container.py`) enforces the base-image digest pin, non-root user
  and exec-form entrypoint.
- **Container release** (`.github/workflows/release-container.yml`): on a `v*` tag, runs the same
  release gates as the PyPI flow (tag matches `__version__`; provenance-honesty gate), builds a
  candidate image, **scans it (trivy) before any public tag is assigned**, then publishes to
  `ghcr.io/guymano/focus-data-toolkit` — **immutable** `<version>` and `sha-<full-commit>` tags
  plus a **rolling** `<major>.<minor>` alias (PEP 440 tag parsing; no `latest`) — generates a
  CycloneDX SBOM, attests build provenance and **signs with cosign** (keyless OIDC), in a
  reviewer-gated `ghcr` environment. All actions pinned by commit SHA.

### Added — progress, cancellation, disk budgets & pipeline ergonomics (deployment Lot A)

- **Progress reporting**: the streaming engine (`convert_files`) accepts an optional
  `progress` callback receiving throttled `ProgressEvent`s per phase (`READING`,
  `TRANSFORMING`, `AGGREGATING`, `WRITING`, `VALIDATING`, `PUBLISHING`) with a completed
  count, an optional total, a unit (`rows`/`bytes`) and a message — derived without
  materialising data (CSV byte cursor / Parquet footer row count). `focus-toolkit convert
  --progress` renders a single throttled status line on stderr. All hooks are opt-in and
  keyword-only; output is byte-identical with or without them.
- **Cooperative cancellation**: `convert_files(..., cancel=...)` checks a predicate between
  rows and validation passes and raises `ConversionCancelled` — the atomic staging directory
  is removed, so nothing partial is ever published. The CLI maps SIGINT/SIGTERM to a clean
  cancel (exit code **130**), so `Ctrl-C` and `docker stop` unwind cleanly instead of dying
  mid-write.
- **Separate disk budgets** (`focus_data_toolkit.runtime`): the scratch filesystem and the
  output filesystem are budgeted independently via `FOCUS_TOOLKIT_WORK_DIR`,
  `FOCUS_TOOLKIT_MAX_WORK_BYTES`, `FOCUS_TOOLKIT_MIN_WORK_FREE_BYTES` and
  `FOCUS_TOOLKIT_MIN_OUTPUT_FREE_BYTES` (`FOCUS_TOOLKIT_LOG_LEVEL` too). A best-effort
  pre-flight (estimate with a safety margin) plus periodic in-run checks fail fast with a
  structured `FDT-IO-005` (output) / `FDT-IO-006` (work / temp budget) diagnostic and CLI
  exit code **5**, instead of a raw `OSError` mid-run. `WORK_DIR` relocates the SQLite
  aggregation + bundle-spill scratch off the output disk (business artifacts stay
  byte-identical; scratch is always cleaned up).
- **Pipeline-friendly exit codes**: `focus-toolkit convert --exit-policy pipeline` maps the
  functional-but-complete outcomes (3 = strict incomplete, 4 = synthetic assumptions) to 0,
  so orchestrators (Kubernetes / Airflow / Jenkins / AWS Batch) don't flag a legitimate run
  failed. The default `detailed` policy keeps the historic codes; full status stays in the
  manifest and `_run.json`.
- **New CLI commands**: `focus-toolkit detect` (dataset/version of a file header, text/JSON),
  `focus-toolkit validate-bundle` (cross-dataset validation gate over explicit per-dataset
  files or an auto-detected `--directory`; ambiguous combinations refused), and
  `focus-toolkit version`. New SDK exports: `ProgressEvent`, `ConversionCancelled`.

### Added — provider-native supplement adapters

- **Adapters** translate documented cloud-provider export formats into the
  canonical supplement kinds automatically, so a client passes native exports
  straight to `convert` / `supplements validate` without renaming anything.
  First adapters (AWS): `aws-invoice-summary` (Invoicing API `InvoiceSummary`,
  incl. nested `Entity.InvoicingEntity`, `DueDate`, `PurchaseOrderNumber`) →
  `invoice`; `aws-savings-plans` (Savings Plans inventory: `paymentOption`,
  `state`, `start`) → `contract_commitment`. Each adapter is a vendored,
  versioned JSON mapping table with official-doc provenance
  (`supplement/adapters/adapters_provenance.json`, sha256-verified); the format
  is auto-detected from the header (force with `FILE:<adapter-name>`).
  Translated rows flow through the unchanged supplement validation and carry
  `ENRICHED` lineage attributed as `supplement:<adapter>@<version>:<file>`. An
  adapter only maps fields its table describes (residual gaps are reported, not
  guessed); an unrecognized export falls back to the generic FOCUS-named path.
  New command: `fdt supplements adapters`. Adapters ship for **AWS**
  (`aws-invoice-summary`, `aws-savings-plans`), **Azure** (`azure-invoice` —
  Billing Invoices REST API; `InvoiceStatus` Due/OverDue/Paid → `Issued`,
  Void → `Voided`) and **GCP** (`gcp-compute-commitments` — Compute Engine
  `regionCommitments`; `status` and CUD payment facts → `contract_commitment`).

### Added — supplemental client data (promise #3)

- **Gap analysis** (`fdt gaps`): reports, per FOCUS 1.4 dataset, exactly which
  columns block strict production for a given 1.2/1.3 source — computed from
  the converter's own provenance rules and annotated from the embedded model —
  plus ready-to-fill CSV templates per supplement kind. Missing mandatory
  source columns are reported as source-completeness gaps.
- **Supplement bundles**: clients supply the missing provider-issued facts as
  sidecar files (CSV/JSON, gzip ok; kinds `billing_period`, `invoice`,
  `invoice_line`, `contract_commitment`; kind auto-detected from the header or
  forced with `FILE:KIND`). Supplements are validated against the source and
  the model before any use (`FDT-SUPP-0xx`: duplicate keys, unknown columns,
  format/allowed-value violations, orphans, `BilledCost` reconciliation
  conflicts, per-column coverage). Pre-flight command:
  `fdt supplements validate`.
- **ENRICHED conversion**: `convert_to_focus_1_4(..., supplements=...)`
  applies supplied facts with `ENRICHED` lineage and full attribution
  (`supplement:<kind>:<file>` + sha256 in the new manifest `supplements`
  section). At full coverage, **strict mode now produces all four FOCUS 1.4
  datasets** with nothing invented; partial coverage keeps the dataset
  `NOT_PRODUCED` with per-value counters showing how close it is. In strict
  mode uncovered nullable assumed columns are emitted empty (synthetic
  defaults never leak); real issuer-assigned `InvoiceDetailId`s replace the
  locally generated back-links.

### Added — capability profiles

- New `CapabilityProfile` (`focus_data_toolkit.model.capabilities`): an
  explicit, validated declaration of the FOCUS applicability conditions a
  source supports (`SupportsUnitPricing`,
  `SupportsMultiplePricingCategories`). The linter enforces
  conditionally-required columns only for declared conditions; the conversion
  pipeline records the active profile in the manifest (`capability_profile`),
  so an unevaluated condition set is visible instead of silent. CLI:
  repeatable `--supports CONDITION` on `convert` and `validate`.

### Added — per-value lineage counters

- The manifest's produced-dataset entries gain a `lineage_summary` section
  counting, per column, how many values actually took each lineage. Today it
  covers the pricing-currency backfill pair (`PricingCurrency` /
  `PricingCurrencyEffectiveCost`): the headline column lineage stays the
  conservative `DERIVED`, and the summary shows the real observed/backfilled
  mix (e.g. `{"OBSERVED": 99800, "DERIVED": 200}`). Identical in the eager and
  streaming paths; bounded memory (columns × lineage categories).

### Added — official FOCUS JSON schemas

- The four official FOCUS 1.4 JSON object schemas (`ContractApplied`,
  `AllocatedMethodDetails`, `CommitmentProgramEligibilityDetails`,
  `ContractCommitmentApplicability`) are vendored verbatim from the
  specification repository (tag `v1.4`) under
  `focus_data_toolkit/model/json_schemas/`, with a provenance manifest
  (source paths, sha256, CC-BY-4.0 attribution). The linter now evaluates
  every JSON-object column against its official schema — conditional scope
  rules, metric exclusivity, ranges, PascalCase `x_` custom keys — via a
  small dependency-free interpreter of the schema subset; violations surface
  as `official_schema_violation`. Previously only `ContractApplied` was
  deep-validated and `ContractCommitmentApplicability` was only checked to
  be a JSON object.

### Fixed — FOCUS conformance (may change output bytes)

- **Synthetic `ContractCommitmentApplicability`**: the object now declares
  `{"IsComplexScope": true, ...}` — the official object schema requires a scope
  representation (`Inclusions` + `InclusionOperator` become required when no
  scope flag is set), so the previous `x_Source`-only object was normatively
  invalid. The value remains `ASSUMED` and still never passes strict mode.
- **`ContractCommitmentDurationType`**: an unparseable or inverted commitment
  period no longer yields a fabricated `"12 Months"`. The value stays empty
  (not derivable) and the affected rows are reported as an aggregated
  `FDT-CC-001` WARNING; the mandatory-column lint then flags the dataset
  instead of silently publishing an arbitrary duration.

- **1.2 participant-entity migration**: `HostProviderName` is no longer derived
  from the deprecated `PublisherName` (the entity that *produced* the service —
  not the infrastructure host). Per the official FOCUS 1.4 `HostProviderName`
  rules, when the source does not expose the underlying host the value MUST
  match `ServiceProviderName`; a 1.2 source never exposes it, so both columns
  now derive from `ProviderName` and carry `DERIVED` lineage (previously
  `RENAMED`) with the spec rule recorded in the manifest. The per-row provider
  context applies the same rule (`host == service` when the host is not
  exposed; the publisher is never used as a fallback host).

### Added — release pipeline

- Secure release workflows (`.github/workflows/`): a reusable **build-once**
  workflow (`release-build.yml`), a **`release-dry-run.yml`** (no publish, no
  privileged scopes), a tag-triggered **`release.yml`** that attests
  wheel/sdist/SBOM/checksums (GitHub Artifact Attestations, keyless OIDC) and
  publishes via **PyPI Trusted Publishing** in a gated environment, and a
  **`reproducibility.yml`** double-build check. Artifacts flow between jobs by
  digest — the publish job never rebuilds.
- A deterministic **CycloneDX 1.5 SBOM** generator (`scripts/generate_sbom.py`)
  that records the embedded FOCUS 1.4 model as a first-class `data` component
  (CC-BY-4.0 + provenance hash), and an offline **release verifier**
  (`scripts/verify_release.py`) checking `SHA256SUMS`, the SBOM, and version
  consistency. Both are covered by `tests/test_release_tooling.py`.

### Changed — dependencies

- Widened the `parquet` extra to `pyarrow>=15,<26` (the lock resolves to 25.x)
  and `pytest-cov` to `>=5,<8` (dev). The Parquet suite passes unchanged.

### Security

- Resolved **PYSEC-2026-113** by moving the resolved `pyarrow` to `>= 23.0.1`
  (25.x); the `pip-audit` gate now runs with **no `--ignore-vuln` exception**.

## [0.9.0] — 2026-07-17

**First public release.** `0.9.0` is the first version prepared for publication
to PyPI (a deliberate "near-stable" signal; `1.0.0` is reserved for after
real-world feedback — see [docs/versioning.md](docs/versioning.md)). It bundles
all functionality developed across the `0.2.0` (P0) and `0.3.0` (P1) milestones,
detailed in their sections below, and adds the packaging, CI/supply-chain,
governance, and provenance work that makes the project publishable. Publication
itself is performed by the release pipeline (see
[docs/releasing.md](docs/releasing.md)).

### Added — packaging & distribution

- **PyPI-ready packaging**: single-sourced version (`focus_data_toolkit._version`),
  PEP 639 SPDX license metadata (`license = "MIT"` + `license-files`), a PEP 561
  `py.typed` marker with the `Typing :: Typed` classifier, project URLs, and a
  `MANIFEST.in` that ships the sources needed to build and verify from an sdist.
- **Reproducible installs**: a committed `uv.lock` and hash-pinned
  `constraints/*.txt`; the optional `[validator]` extra now resolves
  `focus-validator` from **PyPI** (Python 3.12+) instead of a git URL, so wheels
  and sdists upload and install cleanly. New `[release]` extra (`build`, `twine`).
- **Packaging tests** build and install a wheel **and** an sdist in a clean
  environment and smoke-test the result.

### Added — CI & supply-chain hardening

- Modular workflows: lint, type-check (mypy), a test matrix across
  ubuntu/windows × Python 3.11–3.13, coverage floor, and a packaging job.
- Least-privilege `permissions: contents: read` with per-job elevation; every
  GitHub Action pinned to a full commit SHA (Docker image actions to an
  `@sha256` digest), enforced by `scripts/check_pinned_actions.py` and a test.
- Security scanning: `pip-audit` (pinned), `gitleaks`, `actionlint`, and
  `zizmor`; `Dependabot` for actions and Python dependencies; CodeQL and OpenSSF
  Scorecard workflows (gated behind `workflow_dispatch` until repository code
  scanning is enabled — see [docs/releasing.md](docs/releasing.md)).

### Added — governance & documentation

- `SECURITY.md` (GitHub Private Vulnerability Reporting; the security-vulnerability
  vs FOCUS-conformance-bug distinction; a supported-versions matrix; a "no client
  data" rule), `CONTRIBUTING.md`, a `NOTICE` file, `.github/CODEOWNERS`, and
  GitHub issue / pull-request templates.
- Documentation under `docs/`: `versioning.md`, `compatibility.md` (Python/OS/
  FOCUS matrix, including the Windows streaming limitation), `security-model.md`,
  `releasing.md` (with the operational, owner-only checklist), and
  `model-provenance.md`.
- A test that scans committed fixtures for secrets/PII and requires each fixture
  directory to document its synthetic provenance.

### Added — FOCUS model provenance

- A machine-readable provenance manifest
  (`src/focus_data_toolkit/model/model_provenance.json`) with a JSON Schema
  (`schema/model_provenance.schema.json`) and a verifier
  (`scripts/verify_model_provenance.py`, run in CI). It records the source
  (FinOps FOCUS 1.4 Data Model workbook), the **verified CC-BY-4.0** license, the
  extraction process, and the reproducible output hash. Status is `partial`
  (the source workbook is not redistributed/hashed here); a `partial` → `complete`
  gate blocks a fully-reproducible-provenance claim until the source is hashed.

### Changed

- Project version set to **0.9.0** (first public release).

### Fixed

- The committed FOCUS 1.4 model JSON `source` field pointed at a non-existent
  `docs/focus/…xlsx` path, diverging from what `tools/extract_focus_1_4_model.py`
  emits. It now matches the extractor's output, so re-running the extractor on
  the same workbook reproduces the committed JSON byte-for-byte.

## [0.3.0] — pre-release development (P1)

The 0.3.0 line ("P1") makes the toolkit reliable on **real client data** — consolidated,
multi-provider, multi-issuer, multi-currency, volumetric exports — without re-implementing the
0.2.0 (P0) guardrails. It lands in two phases: **Phase A** (correctness & integrity) and
**Phase B** (scale & realism), both described below.

### Added — schema detection

- **`focus_data_toolkit.schema.detect_focus_schema(headers)`** identifies the FOCUS
  **dataset** (Cost and Usage / Contract Commitment / Billing Period / Invoice Detail) and
  **version** (1.2 / 1.3 / 1.4) with a `confidence`, `exact_match`, and the exact
  `missing` / `additional_focus` / `extension` / `unknown` columns and `ambiguous_candidates`.
  It scores the header against a registry of normative column sets (`schema/registry.py`)
  computed from the committed 1.4 model plus a removed-columns table, so a 1.4 file is never
  taken for 1.3, a 1.3 export missing an optional column is not taken for 1.2, `x_` columns
  never count against a match, and unknown non-`x_` columns are surfaced.
- CLI `convert --source-version` / `--source-dataset` force detection; **strict mode refuses
  an ambiguous or low-confidence source** (clear error). The detection decision is recorded
  in the manifest. `detect_focus_version` is retained as a compatibility wrapper.

### Added — multi-provider context & correct grouping

- **Per-row context** (`focus_data_toolkit.context`): `BillingContext` and `ProviderContext`
  are derived from the whole source, never from the first row. The first-row `_provider_context`
  is gone; billing-period issuer is no longer back-filled from row 0. A bounded per-row
  **context summary** (distinct providers / issuers / accounts / currencies / periods and
  `multi_*` flags) is recorded in the manifest; ambiguous contexts are reported as diagnostics.
- **Invoice Detail now groups on the full business grain** — `(InvoiceIssuerName, InvoiceId,
  BillingAccountId, BillingCurrency, BillingPeriodStart, BillingPeriodEnd, ChargeCategory)` —
  preventing collisions across issuers/accounts/currencies/periods. `InvoiceDetailId` is a
  clearly-local, versioned, collision-safe id (`x_fdt_idl_v1_<hash>` over a JSON-encoded key),
  **never presented as issuer-assigned**.

  *Because the `InvoiceDetailId` value scheme changed, synthetic Invoice Detail (and the Cost
  and Usage back-link) have a new byte baseline as of 0.3.0. Conversion remains deterministic.*

### Added — cross-dataset (bundle) validation

- **`validate_dataset_bundle(bundle)`** (alias `validate_bundle`) validates a bundle of
  datasets **against each other** — deliberately separate from the per-dataset linter, which
  never asserts cross-dataset validity. It reports referential integrity (uniqueness, FKs,
  orphans), issuer/account/currency/period coherence, Cost-and-Usage ↔ Invoice-Detail
  reconciliation (only when Invoice Detail is authoritative, with an explicit rounding
  tolerance), **Split Cost Allocation** (ratios sum to 1, allocated costs sum to the origin,
  ratio in [0,1], consistent method/unit, unique resources, incomplete groups flagged), and
  commitment lifecycle (period/percentage) checks. Findings are grouped by severity
  (error / warning / info / not-executable / not-applicable) and serialise to JSON.

### Added — structured diagnostics & error catalog

- **`focus_data_toolkit.errors.Diagnostic`** carries a stable code, severity, business record
  keys, column, expected/actual, suggestion, provenance and group/join context, and renders to
  JSON, a readable console block, and CSV rows. A stable **`FDT-*` code catalog**
  (`validate/codes.py`) replaces opaque "invalid input" messages.

### Added — atomic writes

- **Results appear in the destination only after everything succeeds.** `write_result` stages
  datasets in a temp directory on the same filesystem, enforces the mandatory lint gate (a
  lint-failing result is never published), then writes the deterministic business manifest, an
  operational `_run.json` sidecar (run id / timestamp / per-file SHA-256, kept **out** of the
  business manifest so dataset bytes stay reproducible) and `SHA256SUMS` last, and publishes
  with a single atomic rename. `io/atomic_writer.py` exposes `AtomicOutputDir` and
  `OnExists` (**refuse** default / replace via crash-safe swap-dir / version). CLI:
  `--on-exists`, `--keep-temp`.

### Added — streaming conversion (Phase B)

- **`focus_data_toolkit.convert.convert_files(cost_and_usage, out_dir, …)`** streams the Cost
  and Usage file **once** and stages Invoice Detail aggregation / Billing Period dedup in a
  throwaway SQLite database (`storage/external_index.py`), so **peak memory is flat regardless
  of row count** — a constant ~64 MB peak process RSS from 50k to 300k rows (6× the rows,
  ×1.05 the memory; `tools/benchmark_streaming.py`), and ~38 MB of traced Python allocations
  that a `slow` test asserts do not scale. Costs are summed with Python `Decimal` over an
  `ORDER BY` (BINARY) scan — never SQL `SUM()` — so the streamed totals are bit-for-bit the
  eager ones.
- **Byte-identical to the in-memory path**: both call the same pure per-row / per-group
  functions (`convert_cost_and_usage_row`, `invoice_detail_row`, `billing_period_row`) and the
  same `assemble_manifest`, so equivalence holds by construction (asserted on the datasets,
  the manifest, and `SHA256SUMS`). A new streaming reader/writer layer (`io/records.py`,
  `io/csv_io.py`) auto-detects gzip input and rejects wrong-field-count rows with a
  line-numbered error. CLI: `convert --stream`.

### Added — Parquet output (Phase B)

- **`--output-format parquet`** / `convert_files(…, output_format="parquet")` writes the
  datasets as Parquet with **exact decimal128** (`io/parquet_io.py`): decimal columns use
  `decimal128(precision, scale)` from a committed, reviewed scale registry
  (`model/focus_1_4_decimal_scale.json`) — never binary float, and a value needing more scale
  than the column allows raises with its line number instead of rounding silently. Dates are
  UTC timestamps, JSON/strings verbatim, empty string ↔ null. Exactness contract: **CSV is
  byte-exact, Parquet is decimal-value-exact**. PyArrow stays an optional `[parquet]` extra
  with a clear install hint; the core stays standard-library only.
- **Partitioning & compression**: `--partition-by` writes the Cost and Usage dataset as a
  Hive-partitioned Parquet tree (`COL=value/…/part-N.parquet`) on low-cardinality String /
  Date-Time columns, keeping memory bounded to one open writer per partition; a high-cardinality
  key is warned about (`FDT-IO-004`) and, past a hard cap, refused (nothing partial is
  published). Partition columns live in the paths (standard Hive), so any `pyarrow.dataset`
  reader — and the toolkit's own read-back lint gate — reconstruct full rows, with the values
  round-tripping exactly (reconstructed as strings). `--compression`
  (snappy default / zstd / gzip / none) and `--target-file-size` (approximate part-file roll)
  tune the layout. The atomic writer now checksums files by path relative to the output root, so
  every partition part appears in `SHA256SUMS`.

### Added — synthetic scenarios & lifecycle (Phase B)

- **`generators/scenarios.py`**: deterministic, provider-agnostic scenario builders —
  `split_allocation_group` (ratios sum to exactly 1, allocated costs sum to exactly the origin,
  last consumer absorbs the residue), `correction_set` (original charge plus signed
  `ChargeClass="Correction"` lines that reference it and record the running auditable net in
  `x_NetCharge`; the original is never overwritten), and `billing_lifecycle_instances` (a
  T0→T6 dataset-instance sequence with only allowed status transitions).
- **`focus_data_toolkit.lifecycle`**: a `DatasetInstance` snapshot structure and
  status-transition checks (`FDT-CORR-004`) from the FOCUS `InvoiceIssueStatus`
  (Open→Issued→Voided) and `BillingPeriodStatus` (Open→Closed) value sets — flagging un-void,
  un-issue and silent reopen, scoped per subject.
- **New correction checks** in `validate_dataset_bundle`: net-sum reconciliation to the
  declared `x_NetCharge` (`FDT-CORR-002`) and duplicate-`x_ChargeKey` overwrite detection
  (`FDT-CORR-003`).

### Fixed — streaming publish integrity (Phase B)

- The streaming path wrote datasets through direct file handles (for bounded memory), which
  bypassed the atomic writer's file registration — so `SHA256SUMS` listed only the manifest and
  the scratch SQLite database was published into the output directory. Produced files are now
  fsync'd and enrolled for checksums, and the scratch DB is deleted before commit; `SHA256SUMS`
  is complete and byte-identical to the eager path. (Regression tests added.)

### Changed

- Version 0.3.0 (P1). `focus-data-toolkit[parquet]` / `[scale]` / `[all]` optional extras
  (PyArrow, used for Parquet); the runtime core remains standard-library only. `pyarrow` is
  added to the `dev` extra so CI runs the Parquet suite rather than skipping it. A `slow`
  pytest marker is added and excluded from the default run. New public API surfaced at the
  package root: `convert_files`, `DatasetInstance`, `check_status_transitions`. Reproducible
  benchmark at `tools/benchmark_streaming.py`.

## [0.2.0] — pre-release development (P0)

The 0.2.0 line makes the toolkit honest about what it produces and fixes real FOCUS
conformance defects.

### Added — modes, provenance & manifest

- **Conversion modes** (`--mode`, `focus_data_toolkit.modes.Mode`):
  - `strict` (**new default**) never invents provider-issued financial facts. A
    canonical FOCUS 1.4 dataset is produced only when every Mandatory non-nullable
    column has a factual lineage. From a Cost-and-Usage source, only Cost and Usage is
    produced; Billing Period, Invoice Detail and the 1.4-expanded Contract Commitment are
    reported `NOT_PRODUCED` with their blocking columns.
  - `synthetic` generates assumed values for demos/tests; affected datasets are written
    with a `synthetic_` filename prefix and marked `PRODUCED_SYNTHETIC`, never fully
    conformant.
- **Value provenance / lineage** (`focus_data_toolkit.provenance`): every produced column
  is classified `OBSERVED` / `RENAMED` / `DERIVED` / `ENRICHED` / `ASSUMED` /
  `UNAVAILABLE`. Any `ASSUMED` column prevents a full-conformance claim.
- **Deterministic conversion manifest** (`focus_1_4_manifest.json`,
  `focus_data_toolkit.manifest`): per-dataset status/conformance/reason and per-column
  lineage. Written on every conversion; `--manifest PATH` writes an extra copy.
- **CLI exit codes**: `0` success without assumptions · `1` lint violation ·
  `2` invalid arguments · `3` incomplete strict result · `4` synthetic result with
  assumptions.

### Changed — behaviour

- **Default conversion behaviour changed**: strict mode no longer emits Billing Period /
  Invoice Detail / Contract Commitment reconstructed from Cost and Usage alone. Use
  `--mode synthetic` for the previous all-four output (now correctly labelled).
- `convert_to_focus_1_4` gained a `mode` parameter; `ConversionResult` gained
  `mode`, `provenance`, `manifest`, `not_produced`, `assumptions_present`.
- README rewritten to separate schema migration / enrichment / synthetic projection.
- In synthetic mode the Cost and Usage `InvoiceDetailId` back-link (a locally generated id)
  is lineage `ASSUMED`, so synthetic Cost and Usage is labelled `PRODUCED_SYNTHETIC` and
  `synthetic_`-prefixed; in strict mode it stays null. `PricingCurrency` /
  `PricingCurrencyEffectiveCost` are lineage `DERIVED` (source value; nulls backfilled), not
  `OBSERVED`.
- Manifest conformance for a factual dataset is set **after** the lint runs:
  `STRUCTURAL_LINT` (passed), `LINT_FAILED` (failed), or `NOT_VALIDATED` (`--no-validate`).
- A derivable dataset whose source yields no rows (e.g. no `InvoiceId`, or an empty Contract
  Commitment source) is reported `NOT_PRODUCED` instead of writing a headerless empty file.

### Fixed — FOCUS conformance of generated JSON columns

- **`ContractApplied`** now uses the spec-correct FOCUS 1.3 object schema: a top-level
  `Elements` array with keys `ContractID`, `ContractCommitmentID`,
  `ContractCommitmentAppliedCost`, `ContractCommitmentAppliedQuantity`,
  `ContractCommitmentAppliedUnit` (previously the wrong `ContractId`/`AppliedCost`… names
  and casing). Applied cost/quantity are emitted as JSON **numbers**.
- **`AllocatedMethodDetails`** emits `AllocatedRatio` and `UsageQuantity` as JSON numbers
  (were quoted strings).
- **`SkuPriceDetails`** treats `StorageClass` and `Redundancy` as FOCUS-defined keys
  (unprefixed), completing the 13-key FOCUS-defined set (were incorrectly `x_`-prefixed).
- **`InvoiceDetailGrain`** (derived Invoice Detail) uses `x_`-prefixed custom keys, as
  required for non-FOCUS-defined Key-Value keys.

  *Because generated data changed, the byte-for-byte output of a given `(rows, seed)` has a
  new baseline as of 0.2.0. Generation remains deterministic.*

### Fixed — linter

- **Scientific notation** is now accepted per FOCUS `NumericFormat` (E-notation `mEn`,
  negative-only exponent sign; `35.2E-7` valid, `35.2E+7` invalid). The old regex rejected
  all scientific notation. Numeric literals reject leading zeros (`01`), which are invalid
  JSON numbers.
- The **`x_` custom-key rule** is enforced across JSON columns via a FOCUS-defined key
  registry (`SkuPriceDetails`, `InvoiceDetailGrain`, `ContractApplied`,
  `AllocatedMethodDetails`, `CommitmentProgramEligibilityDetails`), covering both element
  keys **and top-level custom keys** of array-of-objects columns, and is **not** applied to
  `Tags`/`AllocatedTags` (arbitrary tag keys are allowed).
- `ContractApplied` is structurally validated (Elements, keys, types). Quoted numeric
  strings for metric fields are rejected (the schema types them as JSON numbers), and the
  FOCUS 1.4 metric exclusivity (`ContractAppliedObjectSchema` `oneOf`: cost *xor*
  quantity+unit) is enforced.

### Changed

- The internal validator is repositioned as a **structural + semantic linter**:
  `validate_focus_1_4` → **`lint_focus_1_4_structure`**, with explicit levels
  (`STRUCTURAL_VALID`, `SEMANTIC_VALID`); it never asserts cross-dataset or official
  conformance. `validate_focus_1_4` is retained as a **deprecated alias**.
- Conversion of `ContractApplied` from a 1.3 source now **migrates** it to the 1.4 schema:
  re-cased identifier keys, and — since 1.3 permits all metrics but 1.4's `oneOf` does not
  — a 1.3 element carrying both cost and quantity keeps the cost branch, preserving
  quantity/unit losslessly as `x_ContractCommitmentAppliedQuantity`/`…Unit`.
- New typed API `focus_data_toolkit.convert.contract_applied` (parse / validate / migrate).
- Fixed a whitespace-mismatch bug in the Cost-and-Usage → Invoice Detail back-link key.
- README/docs no longer describe the linter as a full conformance validator.

<!-- Reference links. 0.2.0/0.3.0 were pre-release development milestones and were never tagged
     or published, so only the first public release (0.9.0) has a tag link. -->
[Unreleased]: https://github.com/guymano/focus-data-toolkit/compare/v0.11.0...HEAD
[0.11.0]: https://github.com/guymano/focus-data-toolkit/compare/v0.11.0rc1...v0.11.0
[0.11.0rc1]: https://github.com/guymano/focus-data-toolkit/compare/v0.9.0...v0.11.0rc1
[0.9.0]: https://github.com/guymano/focus-data-toolkit/releases/tag/v0.9.0
