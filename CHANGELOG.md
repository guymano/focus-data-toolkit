# Changelog

All notable changes to this project are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [0.3.0] — unreleased

The 0.3.0 line ("P1") makes the toolkit reliable on **real client data** — consolidated,
multi-provider, multi-issuer, multi-currency exports — without re-implementing the 0.2.0
(P0) guardrails. This entry covers **Phase A** (correctness & integrity); streaming and
Parquet (Phase B) follow.

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

### Changed

- Version bumped to 0.3.0. `focus-data-toolkit[parquet]` / `[scale]` / `[all]` optional extras
  declared (PyArrow, used in Phase B); the core remains standard-library only. A `slow` pytest
  marker is added and excluded from the default run.

## [0.2.0] — unreleased

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
