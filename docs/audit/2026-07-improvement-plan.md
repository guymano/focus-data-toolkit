# Improvement plan — 18 PRs in 5 lots

Companion to [`2026-07-repo-audit.md`](./2026-07-repo-audit.md). Every PR is intended to be
independently green (ruff + mypy + pytest) and reviewable (< ~800 diff lines). Spec references
point at the official
[FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec)
repository (tags `v1.2` / `v1.3` / `v1.4`).

Ordering principle: semantic correctness first (Lot 1 changes output values, and the
supplemental-data feature must be built on correct provenance), then the promise-#3 feature
(Lot 2), then client-data robustness (Lot 3), then release hygiene (Lot 4) and owner-side
operations (Lot 5).

```
Lot 1: PR-1 ─┬─ PR-4
       PR-2 ─┴─ PR-3 ── PR-5
Lot 2: (Lot 1) ── PR-6 ── PR-7 ─┬─ PR-8 ── PR-9
                                └─ PR-9a ── PR-9b   (provider-native adapters)
Lot 3: PR-9 ── PR-11 (do first) ── PR-10     PR-12, PR-13 (independent)
Lot 4: PR-14, PR-15 ── PR-16
Lot 5: owner checklist (no code)
```

---

## Lot 1 — P0 semantic correctness

### PR-1 — Fix the 1.2 participant-entity mapping (blocking)

- **Scope:** `HostProviderName` must never be derived from `PublisherName`. Per
  [`hostprovidername.md` (v1.4)](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec/blob/v1.4/specification/datasets/cost_and_usage/columns/hostprovidername.md),
  when the source does not expose the host, `HostProviderName` **MUST match
  `ServiceProviderName`** (null only where the spec's null criteria apply). New rule for 1.2
  sources: `HostProviderName ← ServiceProviderName` value with
  `ColumnRule(Lineage.DERIVED, "ServiceProviderName; host not exposed by 1.2 source — spec: MUST match ServiceProviderName")`.
  `ServiceProviderName ← ProviderName` stays, but as `DERIVED` (documented semantic shift:
  1.3 replaced `ProviderName` with `ServiceProviderName`; marketplace rows may differ), not
  `RENAMED`. `PublisherName` is dropped as a removed column, never reclassified. 1.3 sources
  keep `OBSERVED` pass-through.
- **Files:** `src/focus_data_toolkit/convert/cost_and_usage.py` (`_DERIVED_FROM_1_2`,
  `cost_and_usage_provenance`, `convert_cost_and_usage_row`); any docstring naming the old
  mapping.
- **Tests:** 1.2 marketplace/SaaS fixture where `PublisherName ≠ ProviderName` → converted row
  has `HostProviderName == ServiceProviderName`, never the publisher; provenance/manifest
  assertions; 1.3 pass-through unchanged; golden manifests regenerated; eager == streaming.
- **Acceptance:** no code path reads `PublisherName` into `HostProviderName`; strict 1.2
  migration no longer publishes a fabricated host as factual.
- **Depends on:** —

### PR-2 — Conformant synthetic Applicability + no fabricated duration

- **Scope:** (1) `_APPLICABILITY` becomes `{"IsComplexScope": true, "x_Source": "..."}` —
  the minimal object that satisfies the official
  [`contractcommitmentapplicabilityobjectschema.json`](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec/blob/v1.4/specification/schemas/datasets/contract_commitment/contractcommitmentapplicabilityobjectschema.json)
  conditional (`Inclusions`+`InclusionOperator` required only when neither scope flag is set);
  lineage stays `ASSUMED`. (2) `_duration_type()` no longer returns `"12 Months"` on
  unparseable/inverted dates: emit empty value + `FDT-CC-001` WARNING diagnostic and let the
  linter flag the mandatory column, instead of a fabricated `DERIVED` value.
- **Files:** `src/focus_data_toolkit/convert/contract_commitment.py`.
- **Tests:** applicability shape assertions (deep-schema validation arrives in PR-3); garbage
  dates produce no `"12 Months"`; valid periods still yield `"1 Month"`/`"12 Months"`/`"36 Months"`.
- **Depends on:** — (parallel to PR-1)

### PR-3 — Embed official JSON schemas; deep-validate every JSON column

- **Scope:** vendor the four official v1.4 object schemas
  (`contractappliedobjectschema.json`, `allocatedmethoddetailsobjectschema.json`,
  `commitmentprogrameligibilitydetailsobjectschema.json`,
  `contractcommitmentapplicabilityobjectschema.json` — from
  [`specification/schemas/datasets/`](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec/tree/v1.4/specification/schemas/datasets))
  under `src/focus_data_toolkit/model/json_schemas/` with a `json_schemas_provenance.json`
  (spec repo path + commit + sha256, mirroring `model_provenance.json` conventions).
  Extend `lint_focus_1_4_structure` to deep-validate every JSON-typed column (conditional
  logic implemented in plain Python, driven by the vendored files — no new runtime
  dependency). Add `ContractCommitmentApplicability` and other missing JSON columns to the
  `x_`-prefix registries in `model/focus_json_keys.py`.
- **Files:** `model/validator.py`, `model/focus_json_keys.py`, new `model/json_schemas/*`,
  `pyproject.toml` (package data), `docs/model-provenance.md` note.
- **Tests:** positive/negative fixtures per schema; the pre-PR-2 `{"x_Source": ...}` shape is
  now a lint ERROR; provenance-hash test for the vendored files.
- **Depends on:** PR-2 (so the toolkit's own output passes its own new validator).

### PR-4 — Per-value lineage counters

- **Scope:** converters record actual per-row lineage outcomes for columns whose lineage
  varies (today `PricingCurrency`/`PricingCurrencyEffectiveCost`: OBSERVED when present,
  DERIVED when backfilled; later, supplement coverage). Manifest gains per-column
  `lineage_summary` (e.g. `{"OBSERVED": 99800, "DERIVED": 200}`); the headline lineage stays
  the *weakest* present so strict gating remains conservative. Bounded accumulator
  (columns × 6) threaded through both eager and streaming paths.
- **Files:** `provenance.py`, `convert/cost_and_usage.py`, `convert/__init__.py`
  (`assemble_manifest`), `convert/streaming.py`, `manifest.py`.
- **Tests:** mixed fixture (3 observed / 2 backfilled → summary counts); manifest determinism;
  eager == streaming manifests.
- **Depends on:** PR-1 (same provenance code).

### PR-5 — CapabilityProfile for conditional requirements

- **Scope:** new `model/capabilities.py` with frozen `CapabilityProfile(supported_conditions,
  source)`; `lint_focus_1_4_structure(..., profile=...)`; both conversion paths pass a profile
  derived from the conversion context; the active profile is recorded in the manifest so
  "conditions not evaluated" is never silent.
- **Files:** new `model/capabilities.py`, `model/validator.py`, `convert/__init__.py`,
  `convert/streaming.py`, `manifest.py`.
- **Tests:** identical rows lint differently under empty vs. populated profiles; streaming and
  eager use identical profiles.
- **Depends on:** PR-3.

---

## Lot 2 — Promise #3: supplemental client data (new feature)

> **Revised 2026-07-17 (product review).** The primary client journey is a cloud-provider
> customer holding a FOCUS 1.2/1.3 export **plus native provider artifacts** (invoice
> exports, reservation/commitment inventories) from AWS, Azure or GCP — not hand-authored
> FOCUS-named files. Lot 2 therefore ships **two intake paths over one canonical core**:
> provider-native adapters (PR-9a/PR-9b) translate known official export formats into the
> canonical supplement tables automatically, and the generic FOCUS-named path (PR-6/PR-7)
> remains for everything the adapters don't cover (SaaS vendors, other providers, ERP
> extracts). A client with provider exports renames nothing by hand.

New package `src/focus_data_toolkit/supplement/`. Goal: a client with a 1.2/1.3 source learns
*exactly* which facts are missing, supplies them as sidecar files — provider-native exports
where an adapter exists, FOCUS-named templates otherwise — and a **strict** run then
produces all four FOCUS 1.4 datasets with factual (`ENRICHED`) lineage — no code change to the
gate itself, because `ENRICHED` is already in `FACTUAL_LINEAGES` and `strict_blockers` already
unblocks datasets whose mandatory non-nullable columns are all factual.

**Where the missing facts live at the providers** (all officially documented; the adapter
mapping tables in PR-9a/PR-9b are vendored from these sources, like the FOCUS schemas):

| FOCUS 1.4 need | AWS | Azure | GCP |
|---|---|---|---|
| Invoice facts (status, issue/due dates, PO, payment terms, amounts) | [Invoice Summary API](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_invoicing_ListInvoiceSummaries.html) (`ListInvoiceSummaries`: due date, PO number, billing period, base/tax-currency amounts) | [Invoices REST API / MCA invoice documents](https://learn.microsoft.com/en-us/azure/cost-management-billing/understand/mca-understand-your-invoice) (status `Due`/`Past due`/`Paid`, Net-30 terms, due date, billing profile) | [Cloud Billing BigQuery export](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables) (`invoice.month` maps rows to issued invoices; invoice CSV/PDF from the console) |
| Commitment terms (payment model/interval, lifecycle, created/updated) | RI / Savings Plans inventory (Savings Plans API, CUR commitment fields) | Reservations / Savings Plan APIs and exports | CUD metadata export (`cud_subscriptions_export`: commitment amounts, consumption model, periods) |
| Billing-period facts (status, created/last-updated) | derived from the invoice artifacts above | idem | idem |

**Verified gap analysis** (from the embedded model + real provenance dicts —
`gap = Mandatory ∧ ¬allows_nulls ∧ ¬factual`, i.e. exactly `strict_blockers()`):

| 1.4 dataset | Derivable from source | Must be supplied by the client |
|---|---|---|
| Billing Period | `BillingPeriodStart/End`, `InvoiceIssuerName` | `BillingPeriodCreated`, `BillingPeriodLastUpdated`, `BillingPeriodStatus` |
| Invoice Detail | `InvoiceId`, `ChargeCategory`, `BillingAccountId`, `BillingCurrency`, period bounds, issuer; `BilledCost` (exact grain sum) | Header-level: `InvoiceIssueStatus`, `PaymentTerms`, `ReferenceInvoiceId` (+ recommended `InvoiceIssueDate`, `PaymentDueDate`, `PurchaseOrderNumber`, `PaymentCurrency`). Line-level: `InvoiceDetailId`, `InvoiceDetailCreated/LastUpdated` (+ `InvoiceDetailDescription`, `InvoiceDetailGrain`, payment-currency costs) |
| Contract Commitment (30 cols in 1.4) | 14 observed + 3 derived from a 1.3 CC source; issuer/provider from context | `ContractCommitmentCreated/LastUpdated`, `ContractCommitmentApplicability` (official JSON object), `BenefitCategory`, `FulfillmentInterval`, `LifecycleStatus`, `Model`, `OfferCategory`, `PaymentInterval`, `PaymentModel` (+ nullable `DiscountPercentage`, conditional `PaymentUpfrontPercentage`); or the whole dataset if no 1.3 CC source exists |
| Cost and Usage | strictly producible already | optional: real `InvoiceDetailId` back-links replace synthetic `x_fdt_idl_v1_*` ids |

### PR-6 — Gap analysis engine + `fdt gaps` command

- **Scope:** `supplement/gaps.py` (`compute_gaps(source_columns, source_version, cc_columns=None)
  -> GapReport`; `ColumnGap` carries dataset, column, feature level, current lineage, blocking
  flag, satisfying supplement kind, join keys, allowed values, condition text, spec reference)
  and `supplement/kinds.py` (registry: kind name, target dataset, join keys, allowed columns).
  Read-only. CLI `fdt gaps --cost-and-usage src.csv [--contract-commitment cc.csv]
  [--format text|json]`; the JSON output doubles as a fill-in template including ready-to-use
  CSV header lines per kind.
- **Files:** `supplement/{__init__,kinds,gaps}.py`, `cli.py`, new `docs/supplements.md`.
- **Tests:** gap reports for 1.2 vs 1.3 vs 1.3+CC sources; property test: every reported
  blocking gap is genuinely non-factual per `strict_blockers`; stable JSON schema.
- **Depends on:** PR-1/2/4 (final provenance dicts), PR-5 (condition surfacing).

### PR-7 — Supplement bundle format, loader, cross-validation

- **Scope:** `supplement/spec.py` (bundle manifest `supplements.json`: path, kind,
  free-text provenance, `as_of`), `supplement/loader.py` (`SupplementBundle.load`; CSV via
  `CsvRowReader` (gzip-aware), JSON sidecars; kind resolution: explicit `:kind` suffix wins,
  else header matching — ambiguity is a hard error; provider-native header detection is
  added on top by the PR-9a adapters), `supplement/validate.py` with
  `FDT-SUPP-0xx` diagnostics: duplicate join keys (001, ERROR), missing join-key column (002,
  ERROR), unknown non-`x_` column (003, ERROR), value fails model type/allowed values (004,
  ERROR, reusing `model/validator.py` checkers), orphan rows (005, WARNING), conflicts with
  source facts, e.g. `BilledCost` vs grain sums via `validate/reconciliation.py` (006, ERROR),
  coverage gaps with counts (010, informational, drives gating). Standalone pre-flight command
  `fdt supplements validate`. Join keys compared after the converters' `.strip()`
  normalization; no fuzzy date matching — mismatch is a diagnostic, never a guess.
- **Files:** `supplement/{spec,loader,validate}.py`, `cli.py`.
- **Tests:** happy path per kind; each diagnostic class; gzip CSV; JSON sidecar.
- **Depends on:** PR-6.

### PR-8 — Wire supplements into the eager converter + manifest attribution

- **Scope:** `convert_to_focus_1_4(..., supplements=None)`; `supplement/apply.py` merges facts
  into Billing Period / Invoice Detail / Contract Commitment construction. A supplied column's
  rule becomes `ColumnRule(Lineage.ENRICHED, "supplement:<kind>:<file>")` **only at 100 %
  key coverage**; partial coverage keeps the headline lineage non-factual (dataset stays
  `NOT_PRODUCED` in strict mode, with `FDT-SUPP-010` and per-value counters from PR-4 showing
  how close the client is). Full-dataset supply (`dataset:<name>` kind) passes validated rows
  through with mandatory reconciliation against Cost and Usage. Manifest gains a top-level
  `supplements` section (path, kind, sha256, row counts, rows_matched, columns, provenance,
  as_of) and per-column supplement attribution.
- **Files:** `supplement/apply.py`, `convert/__init__.py`, `convert/billing_period.py`,
  `convert/invoice_detail.py`, `convert/contract_commitment.py`, `manifest.py`.
- **Tests:** end-to-end: 1.2 source + complete supplement set → **strict** run produces all
  four datasets, `status=PRODUCED`, lint green, zero `ASSUMED`; partial coverage →
  `NOT_PRODUCED` + missing-key report; conflicting supplement refused; manifest determinism.
- **Depends on:** PR-7.

### PR-9 — Streaming supplements + CLI surface

- **Scope:** `convert_files(..., supplements=...)`: small kinds held in dicts; `invoice_line`
  and `dataset:*` kinds indexed through `ExternalIndex` (SQLite) keyed by the same grain hash
  as `invoice_detail_id()` so eager and streaming stay byte-identical with bounded memory.
  CLI: `fdt convert ... --supplement FILE[:KIND]` (repeatable) and `--supplements-dir DIR`.
- **Files:** `convert/streaming.py`, `storage/external_index.py`, `cli.py`,
  `supplement/loader.py`.
- **Tests:** eager-vs-streaming equivalence with supplements; large line-supplement fixture
  exercising the SQLite path; CLI parsing.
- **Depends on:** PR-8.

### PR-9a — Provider adapter framework + AWS adapters

- **Scope:** `supplement/adapters/` — a declarative adapter layer that recognizes **official
  provider export formats by their native headers/fields** (same scoring mechanic as
  `detect_focus_schema`) and translates them into the canonical supplement tables of PR-7.
  An adapter is data, not code: a vendored mapping table (`adapters/aws_invoice.json`, …)
  recording source format + version, native column → FOCUS column, native value → FOCUS
  allowed value (e.g. an invoice status vocabulary → `InvoiceIssueStatus`), join-key
  construction, plus a provenance block (official doc URL, retrieval date, sha256) — the same
  vendoring discipline as `model/json_schemas/`. First adapters: **AWS** invoice summaries
  (`ListInvoiceSummaries` JSON/CSV: due date, PO number, billing period, amounts → `invoice`
  kind) and AWS commitment inventories (RI/Savings Plans → `contract_commitment` kind).
  UX: `fdt convert ... --supplement aws_invoices.json` — the adapter is auto-detected;
  `:aws-invoice` forces it. Translated tables then flow through the **unchanged** PR-7
  validation (`FDT-SUPP-0xx`) — adapters never bypass it. An unrecognized file falls back to
  the generic FOCUS-named path with a clear diagnostic (`FDT-SUPP-020`, listing the adapter
  candidates that were considered); never a silent guess. Manifest attribution becomes
  `supplement:<adapter>@<mapping-version>:<file>` so every ENRICHED value is auditable back
  to the native provider artifact.
- **Files:** `supplement/adapters/{__init__,registry}.py`, `supplement/adapters/*.json`
  (mapping tables + provenance), `supplement/loader.py` (detection hook), `cli.py`,
  `docs/supplements.md` (per-adapter "how to export this from your provider" walkthrough).
- **Tests:** fixture files mirroring the documented AWS output shapes (synthetic values);
  auto-detection + forced-kind; value-vocabulary translation; unrecognized file → clean
  fallback diagnostic; adapter mapping-table provenance hashes.
- **Depends on:** PR-7 (canonical tables + validation); delivers end-user value with PR-8/9.

### PR-9b — Azure and GCP adapters

- **Scope:** same framework, two more provider families: **Azure** invoice data (Invoices
  REST API / MCA-EA invoice detail exports: status `Due`/`Past due`/`Paid` →
  `InvoiceIssueStatus`, Net-terms → `PaymentTerms`, due date, billing profile → issuer) and
  reservation/savings-plan exports → `contract_commitment`; **GCP** Cloud Billing BigQuery
  exports (`invoice.month` grouping → billing-period/invoice facts) and the CUD metadata
  export (`cud_subscriptions_export` → `contract_commitment`). Account-type variants (Azure
  EA vs MCA; GCP standard vs detailed export) are **distinct versioned mapping tables**, each
  with its own provenance block — a format the tables don't cover falls back cleanly rather
  than half-matching.
- **Files:** `supplement/adapters/azure_*.json`, `supplement/adapters/gcp_*.json`, fixtures.
- **Tests:** per-format fixtures (synthetic); EA-vs-MCA disambiguation; vocabulary
  translations; fallback on unknown variants.
- **Depends on:** PR-9a.

**Honesty rules for adapters** (both PRs): provider export formats vary by account type and
evolve over time — an adapter only ever claims the formats its vendored, versioned mapping
tables describe, translated values keep `ENRICHED` lineage with full attribution (adapter id,
mapping version, source file sha256), and anything unrecognized is a visible diagnostic plus
generic-path fallback, never an inference.

---

## Lot 3 — P1 client-data robustness

> **Revised 2026-07-18 (post-Lot-2).** Lot 2 shipped and changes what Lot 3 builds on:
> (a) the streaming path now reads the Cost and Usage source **twice** (a supplement
> key-collection pre-pass, `pre = CsvRowReader(cost_and_usage)`) and the supplement loader's
> `_read_rows` gained JSON / gzip / envelope / adapter branches — PR-10 must cover both;
> (b) strict mode can now **produce all four datasets** from supplements, so cross-dataset
> bundle validation (PR-11) is the guard for that newly-unlocked output and is the
> highest-value item of the lot; (c) Lot 2 already built the bounded-memory key-set
> machinery (`SourceKeySets` / `source_key_sets` / `coverage`, with `ExternalIndex` spill)
> and a `Diagnostic`/`FDT-*` channel — PR-11 reuses these rather than inventing new ones;
> (d) `_fsync_file` is already best-effort after the Lot-2 Windows fix, which PR-13 assumes.
> **Recommended order: PR-11 first** (it protects the four-dataset strict output), then
> PR-10, PR-12, PR-13. No new PRs; scope adjustments only.

### PR-11 — Streaming bundle validation as a publication gate  *(do first)*
Run cross-dataset validation as a mandatory gate in `write_result` and `convert_files`, in the
staging directory **before** `AtomicOutputDir.commit` — ERROR diagnostics block publication
(escape hatch `--no-validate`, recorded in the manifest), and the bundle result is written into
the manifest. This is now the main correctness guard for a strict run that produces all four
datasets from supplements (referential integrity, billing-period coverage, correction net
sums).
- **Reuse, don't reinvent:** consume the datasets as iterables with bounded key sets built on
  Lot 2's `SourceKeySets`/`coverage` pattern, spilling to `ExternalIndex` above a threshold;
  emit findings through the existing `Diagnostic`/`FDT-*` channel alongside the per-dataset
  lint gate (not a parallel mechanism).
- **Reconciliation decision (settled):** Invoice Detail stays **non-authoritative** for cost
  reconciliation while its `BilledCost` is the derived grain sum — supplements add invoice
  *metadata* (status/dates/terms/real ids) but never replace `BilledCost` (the `invoice_line`
  `BilledCost` is only an `FDT-SUPP-006` consistency check), so running
  `reconcile_invoice_detail` would still be circular. Authoritative cost reconciliation is
  deferred to a future generic `dataset:invoice_detail` full-supply kind (out of scope here).
  The valuable cross-dataset checks post-Lot-2 are the **referential** ones (every Cost-and-
  Usage `InvoiceDetailId` — now a real client id from an `invoice_line` supplement — resolves
  in Invoice Detail; every source period is covered by a Billing Period row).
- Distinguish checks applicable to synthetic vs. factual datasets.
- Tests: a dangling `InvoiceDetailId` blocks publication; a supplement-produced four-dataset
  strict bundle passes; memory-bound test on a large fixture.
- **Depends on:** PR-9 (supplement-produced bundles are the customer; reuses its key-set/
  coverage machinery).

### PR-10 — Parquet input
`RowSource` protocol over `CsvRowReader`/`ParquetRowReader` (both already exist); source format
by extension/sniff (`auto|csv|parquet`), including Hive-partitioned datasets via
`PartitionedParquetReader`.
- Scope broadened by Lot 2: the streaming **supplement pre-pass** and `_lint_file`'s output
  re-read must go through the same `RowSource`, and the supplement loader's `_read_rows` (now
  CSV/JSON/gzip/adapter) grows a Parquet branch.
- Priority: the **Cost-and-Usage source** in Parquet is the high-value case. Parquet
  *supplements* are low value (provider exports — invoice summaries, savings-plans — are
  JSON/CSV), so support them only if it falls out of the `RowSource` refactor for free.
- Tests: a Parquet source converts byte-identically to its CSV twin, with and without
  supplements. **Depends on:** PR-9 (streaming pre-pass), PR-11 (bundle gate runs on the
  produced datasets regardless of input format).

### PR-12 — Complete lifecycle-chain validation
`lifecycle.py`: `instance_id` and per-subject `order` uniqueness, `previous_instance_id`
resolution, cycle detection, `last_updated` monotonicity, closed-instance immutability — all as
diagnostics. Synergy (not scope change): Billing Period / Invoice Detail now carry real
supplement-supplied `Created`/`LastUpdated`/`Status`, so these chains are worth validating on a
supplement-produced bundle. **Depends on:** —

### PR-13 — Transactional replace + crash recovery
`AtomicOutputDir`: journal the replace sequence; on entry, detect leftover `.trash-*` /
`.output.tmp-*` and roll forward or clean with a WARNING; optional `fdt clean --out DIR`.
Remove/qualify "crash-safe swap" wording until then (note: `_fsync_file` is already best-effort
after the Lot-2 Windows fix — this PR is about the multi-rename transaction, not fsync).
Fault-injection tests between the two renames. **Depends on:** —

---

## Lot 4 — P2 release & supply chain

### PR-14 — CI & typing
Restore CodeQL/Scorecard schedule+PR triggers (repo is public); remove mypy `ignore_errors`
for `convert.streaming` then `generators.*` (fix fallout, module by module); add the
`Programming Language :: Python :: 3.13` classifier. **Depends on:** —

### PR-15 — Packaging & honesty
Add `NOTICE` + a model-specific license file (CC-BY-4.0) to `license-files` with a
wheel-contents test; align the pyproject description and package/CLI docstrings with the
README's positioning ("produces the four 1.4 datasets when the source or client supplements
permit" — literally true after PR-8); complete `model_provenance.json` (workbook sha256,
retrieval date, byte-for-byte re-extraction → `provenance_status: "complete"`).
**Depends on:** — (docstring wording references Lot 2 but does not require it)

### PR-16 — Release pipeline
Add a `github-release` job: create a GitHub Release on tag, attach wheel/sdist/SBOM/
SHA256SUMS/release-manifest.json/model_provenance.json, attest all of them, notes from the
changelog; lock the release build backend (exact setuptools/build versions + hashes wired into
the isolated build); resolved SBOM profiles (exact versions, purls, hashes, licenses,
transitive tree) alongside the declared-deps SBOM. **Depends on:** PR-14/15.

---

## Lot 5 — Operations (owner-only, no code)

Tick the `docs/releasing.md` "Operationally Ready" checklist: PyPI Trusted Publishing +
`pypi` environment, branch/tag rulesets, Private Vulnerability Reporting, secret scanning +
push protection, Dependency Graph + code scanning (required checks include CodeQL/Scorecard
after PR-14), run the release dry-run, then the first 0.9.x beta publication. `1.0.0` only
after Lots 1–4 are merged and the checklist is fully green.

---

## Reused existing utilities (no reinvention)

`strict_blockers` + `ColumnRule`/`FACTUAL_LINEAGES` (gating — unchanged by design),
`assemble_manifest` (extended), `GRAIN_FIELDS`/`invoice_detail_id`/`invoice_detail_row`
(supplement join grain), `CsvRowReader` (gzip supplement reading), `ExternalIndex` (large
supplements, streaming joins, bundle-validation spill), `detect_focus_schema` header-scoring
pattern (kind detection), `model/validator.py` column checkers + `load_model()` (supplement
value validation, gap enumeration), `validate/referential.py` + `validate/reconciliation.py`
(supplement cross-validation), `Diagnostic`/`Severity`, `AtomicOutputDir`, `manifest.render`
determinism.
