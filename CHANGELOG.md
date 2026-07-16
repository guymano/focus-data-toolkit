# Changelog

All notable changes to this project are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

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
