# Repository audit — verified against code and the official FOCUS specification

- **Repository:** `guymano/focus-data-toolkit`
- **Commit audited:** `a7a7dbe66fcebf3c15ac75c7b9a2a4ab9912e54a` (`main` HEAD at audit time)
- **Date:** 2026-07-17
- **Method:** every finding below was re-verified directly in the source tree at the audited
  commit (file/line references), and every normative claim was checked against the official
  FinOps Foundation FOCUS specification repository
  ([FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec)),
  tags `v1.2`, `v1.3` and `v1.4` (v1.4 ratified 2026-06-04 — see
  [Introducing FOCUS 1.4](https://www.finops.org/insights/introducing-focus-1-4/)).
  An earlier external audit report was used as input; all of its technical findings were
  independently confirmed, with two minor corrections noted in §6.

---

## 1. The project promise, evaluated

The project promises to:

1. **Generate realistic synthetic FOCUS data in the 1.2 and 1.3 formats** so anyone can use
   FOCUS sample data for their own needs and projects.
2. **Convert FOCUS 1.2/1.3 data (synthetic or real) to FOCUS 1.4**, while being explicit that
   a complete, faithful, conformant FOCUS 1.4 bundle generally *cannot* be produced from older
   data alone, because several facts required by the new datasets did not exist in older
   versions.
3. **Let the client supply additional data** so the conversion *can* produce all four
   FOCUS 1.4 datasets — which requires identifying exactly which supplemental facts complete a
   1.2/1.3 source and how to process them.

### Verdict per promise

| Promise | Verdict | Basis |
|---|---|---|
| 1 — Synthetic 1.2/1.3 generation | **Held.** | Generators cover AWS/Azure/GCP profiles for 1.2 and 1.3 with a shared engine, version adapters, golden byte-for-byte snapshots, and official column names (e.g. `ContractApplied` uses the FOCUS 1.3 key set). |
| 2 — Honest 1.2/1.3 → 1.4 conversion | **Held architecturally; two semantic defects block strict 1.2 migration.** | Strict-by-default mode, six-category lineage, per-dataset manifest, and a validator repositioned as a structural/semantic linter all implement the honesty principle. But the 1.2 participant-entity mapping violates the spec (§3.1) and the synthetic `ContractCommitmentApplicability` object violates the official JSON schema (§3.2). |
| 3 — Client-supplied supplemental data → full factual 1.4 bundle | **Not implemented.** | No code path accepts supplemental client facts; strict mode simply refuses to produce Billing Period / Invoice Detail / enriched Contract Commitment, and synthetic mode fabricates them with `ASSUMED` lineage. There is no "gap report" identifying what a client must supply. This is a new feature, not a fix — see the companion improvement plan (Lot 2). |

FOCUS 1.4's four datasets are confirmed in the official repository:
`specification/datasets/{cost_and_usage, invoice_detail, billing_period, contract_commitment}`
([v1.4 tree](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec/tree/v1.4/specification/datasets)).
The 1.4 release adds Invoice Detail and Billing Period, grows Contract Commitment from 13 to 30
columns, and formalizes JSON object schemas — including facts (payment terms, invoice issue
status, commitment lifecycle status, applicability scope…) that are simply absent from any
1.2/1.3 Cost and Usage export. The project's core honesty stance is therefore correct and
spec-aligned.

---

## 2. What is verifiably good

All confirmed in code at the audited commit:

- **Strict mode by default** (`src/focus_data_toolkit/modes.py`): strict runs never invent
  Billing Period or Invoice Detail, never emit the enriched Contract Commitment when it would
  contain mandatory assumptions, write a manifest explaining every `NOT_PRODUCED` dataset, and
  exit with a distinct code. Synthetic outputs are opt-in and filename-prefixed.
- **Six-category lineage** (`src/focus_data_toolkit/provenance.py`): `OBSERVED / RENAMED /
  DERIVED / ENRICHED / ASSUMED / UNAVAILABLE`; `strict_blockers()` prevents a dataset with a
  mandatory, non-nullable, non-factual column from being published as strict output.
- **Validator honesty** (`src/focus_data_toolkit/model/validator.py`): claims only
  `STRUCTURAL_VALID` / `SEMANTIC_VALID`, explicitly not `CROSS_DATASET_VALID` or
  `OFFICIALLY_VALIDATED`; deprecated alias retained.
- **Schema/version detection** (`src/focus_data_toolkit/schema/detection.py`): header scoring
  against a registry with confidence, missing/foreign/unknown columns and ambiguity reporting.
- **Bounded-memory streaming conversion** (`src/focus_data_toolkit/convert/streaming.py` +
  `storage/external_index.py`): single-pass CSV read, SQLite staging for Invoice Detail
  aggregation and Billing Period dedup, Decimal-exact sums, chunked lint, byte-identical to the
  eager path; gzip CSV input; CSV/Parquet output including Hive-partitioned trees.
- **Atomic publication** (`src/focus_data_toolkit/io/atomic_writer.py`): staging directory,
  per-file fsync, SHA256SUMS, path-traversal guard, refuse/replace/version modes.
- **Cross-dataset validation library** (`src/focus_data_toolkit/validate/`): ID uniqueness,
  foreign keys, Cost-and-Usage↔Invoice-Detail consistency, billing-period coverage,
  authoritative reconciliation (correctly disabled for derived Invoice Detail to avoid circular
  proof), split-cost allocation, corrections.
- **CI and supply chain**: ruff, mypy, Linux+Windows × Python 3.11–3.13, packaging tests,
  SHA-pinned actions (enforced by script and test), pip-audit / gitleaks / actionlint / zizmor,
  reproducible wheel check, build-once release pipeline with OIDC Trusted Publishing and
  attestations, strong governance docs (SECURITY.md, CODEOWNERS, versioning/compatibility/
  release guides), and a model-provenance manifest that does not overclaim.

---

## 3. Blocking and major findings (verified)

### 3.1 BLOCKING — 1.2 participant-entity mapping contradicts the specification

`src/focus_data_toolkit/convert/cost_and_usage.py:27-31`:

```python
_DERIVED_FROM_1_2 = {
    "ServiceProviderName": "ProviderName",
    "HostProviderName": "PublisherName",
}
```

and lines 59-60 classify both as `Lineage.RENAMED` — a *factual* lineage — so a strict 1.2
migration publishes `HostProviderName = PublisherName` as if it were a faithful rename.

**Official definitions:**

- `PublisherName` (v1.2, [`specification/columns/publisher.md`](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec/blob/v1.2/specification/columns/publisher.md)):
  *"The name of the entity that produced the resources or services that were purchased."*
- `HostProviderName` (v1.4, [`specification/datasets/cost_and_usage/columns/hostprovidername.md`](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec/blob/v1.4/specification/datasets/cost_and_usage/columns/hostprovidername.md)):
  *"The name of the entity whose resources are used by the Service Provider to make their
  resources or services available"* — with the normative rules that it MUST reflect the host
  when the customer selected it or the service provider exposes it, MAY be null when there is
  no underlying infrastructure or the host cannot be determined, and otherwise
  **"MUST match ServiceProviderName in all other cases."**
- `ServiceProviderName` (v1.4, [`serviceprovidername.md`](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec/blob/v1.4/specification/datasets/cost_and_usage/columns/serviceprovidername.md)):
  MUST NOT be null; introduced in 1.3 as the replacement for the removed `ProviderName`; in
  marketplace scenarios it denotes the seller.

A SaaS publisher's software hosted on AWS/Azure/GCP has `PublisherName ≠ HostProviderName`.
A 1.2 source never exposes the underlying host, so the only spec-sanctioned derivations are
`HostProviderName = ServiceProviderName` (the "all other cases" rule) or null where the null
criteria apply — never `PublisherName`. The current mapping can fabricate an incorrect host,
label it factual, pass strict mode, and present the migration as faithful.

**Consequence:** strict migration of FOCUS 1.2 sources is a **NO-GO** until fixed (improvement
plan PR-1). Note that `providername.md`/`publishername.md` still exist (deprecated) in the
[v1.3 column set](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec/tree/v1.3/specification/datasets/cost_and_usage/columns)
and are removed in 1.4, so `PublisherName` must be dropped, not reclassified.

### 3.2 MAJOR — synthetic `ContractCommitmentApplicability` violates the official JSON schema

`src/focus_data_toolkit/convert/contract_commitment.py:67-71` emits:

```json
{"x_Source": "Derived from a FOCUS 1.3 Contract Commitment dataset; ..."}
```

The official schema
([`specification/schemas/datasets/contract_commitment/contractcommitmentapplicabilityobjectschema.json`](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec/blob/v1.4/specification/schemas/datasets/contract_commitment/contractcommitmentapplicabilityobjectschema.json), v1.4)
requires: when neither `IsGlobalScope` nor `IsComplexScope` is true, `Inclusions` (min 1) and
`InclusionOperator` are required. An object containing only `x_Source` does **not** validate.
A minimal conformant synthetic object is `{"IsComplexScope": true, "x_Source": "..."}` with
lineage kept `ASSUMED`.

**Aggravating factor:** the internal linter deep-validates only `ContractApplied`
(`model/validator.py:213-214`); every other JSON column gets shallow checks (valid JSON object,
`x_` prefix, Elements container), and `ContractCommitmentApplicability` is not even in the
`x_`-prefix registries (`model/focus_json_keys.py`) — its only check is "is a JSON object". The
toolkit can therefore stamp `SEMANTIC_VALID` on a dataset containing a normatively invalid
object. The official v1.4 schemas to embed and enforce are published in the spec repository:
`contractappliedobjectschema.json`, `allocatedmethoddetailsobjectschema.json`,
`commitmentprogrameligibilitydetailsobjectschema.json`
([cost_and_usage](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec/tree/v1.4/specification/schemas/datasets/cost_and_usage))
and `contractcommitmentapplicabilityobjectschema.json` (contract_commitment).

### 3.3 MAJOR — lineage is column-level only

`provenance.py` models one `ColumnRule` per column ("uniform across rows" per its docstring);
the manifest (`manifest.py:46`) records a single lineage per column. Yet e.g.
`PricingCurrency` / `PricingCurrencyEffectiveCost` are globally `DERIVED`
(`convert/cost_and_usage.py:51-56`) even though most rows are copied verbatim (observed) and
only null rows are backfilled (lines 119-122). The manifest cannot show how many values were
observed vs. reconstructed vs. unavailable. Fix: per-column `lineage_summary` counters (PR-4).

### 3.4 MAJOR — conditional requirements disabled by default

`lint_focus_1_4_structure(..., supported_conditions=None)` defaults to an empty set
(`model/validator.py:370-389`), and the production streaming path passes none
(`convert/streaming.py:152`). Conditionally-required columns are thus never enforced in the
pipeline, and strict mode's gate (`strict_blockers`) only covers Mandatory + non-nullable +
non-factual columns. Fix: explicit `CapabilityProfile` recorded in the manifest (PR-5).

### 3.5 SECONDARY — fabricated contract duration on invalid dates

`_duration_type()` (`convert/contract_commitment.py:94-100`) returns `"12 Months"` whenever the
period cannot be parsed (or is inverted), while `ContractCommitmentDurationType` stays
classified `DERIVED` (line 46). An unparseable input must not yield a factual-labelled
arbitrary value (PR-2).

---

## 4. Client-data findings (P1, verified)

1. **Parquet input missing.** `--cost-and-usage` is CSV-only (`cli.py:242`; eager path
   `read_csv_rows`, streaming path `CsvRowReader`); the Parquet readers in `io/parquet_io.py`
   are only used to re-read produced *outputs* for lint. `--output-format parquet` controls
   output only. A client holding a FOCUS Parquet export must convert to CSV first. (PR-10)
2. **Bundle validation is not memory-bounded.** `validate_dataset_bundle`
   (`validate/bundle.py:88-91`) materializes every dataset with `list(...)`. A user can convert
   millions of rows in bounded memory, then lose that guarantee the moment they validate the
   bundle. (PR-11)
3. **Bundle validation is not a publication gate.** No production code path calls
   `validate_dataset_bundle`; only tests do. Both conversion paths lint datasets individually
   and can atomically publish a bundle that is cross-dataset inconsistent. (PR-11)
4. **Lifecycle validation incomplete.** `lifecycle.py` checks only ordered status transitions;
   `instance_id`/`order` uniqueness, `previous_instance_id` validity, cycles and
   `last_updated` monotonicity are unchecked. (PR-12)
5. **Replace mode is not transactional.** `AtomicOutputDir._atomic_publish`
   (`io/atomic_writer.py:214-237`) performs dest→trash, staging→dest, delete-trash as three
   separate operations, with no startup recovery for leftover `.trash-*` / `.output.tmp-*`
   directories. "Crash-safe swap" phrasing should be avoided until a recovery mechanism
   exists. (PR-13)

---

## 5. Release and supply-chain findings (P2, verified)

1. **CodeQL and OpenSSF Scorecard are `workflow_dispatch`-only** — push/PR/schedule triggers
   are commented out pending the repo going public; the repo is public. (PR-14)
2. **No persistent GitHub Release.** The release workflow builds, attests and publishes to
   PyPI, but SBOM / SHA256SUMS / release-manifest.json live only in a 7-day Actions artifact;
   `release-manifest.json` and `model_provenance.json` are not attested at all. (PR-16)
3. **Build backend not locked.** `requires = ["setuptools>=77"]` resolves freshly in the
   isolated build env. Nuance vs. the external audit: `constraints/release.txt` *does* exist
   and pins the `release` extra (build/twine + hashes), but nothing wires any constraint into
   PEP 517 backend resolution, so the conclusion stands. (PR-16)
4. **SBOM is declared-deps-only.** `scripts/generate_sbom.py` reads wheel `Requires-Dist` and
   emits version *ranges* (e.g. `pyarrow >=15,<26`) without resolved versions, hashes,
   licenses, or a transitive tree (only the embedded model component has hash+license). (PR-16)
5. **License packaging.** The wheel ships a CC-BY-4.0-derived FOCUS model, but metadata says
   `license = "MIT"`, `license-files = ["LICENSE"]`; NOTICE is sdist-only and untested in the
   wheel. (PR-15)
6. **Model provenance `partial`.** `model_provenance.json` has `artifact_sha256: null`,
   `artifact_retrieved: null` — honestly documented, but the end-to-end chain is incomplete.
   (PR-15)
7. **Type-checking gaps.** mypy `ignore_errors` covers `convert.streaming` and all
   `generators.*` (`pyproject.toml:103-109`) — precisely the client-data hot path and the most
   dynamic code. (PR-14)
8. **Metadata/docs drift.** Python 3.13 tested in CI but absent from classifiers; the
   pyproject description, package and CLI docstrings still say "convert … to the four FOCUS 1.4
   datasets" without the strict-mode qualification the README carefully makes. Nuance vs. the
   external audit: the phrasing is contextual rather than an outright false claim, but it
   should be aligned — and becomes literally true once supplements land (Lot 2). (PR-14/15)
9. **Operationally Ready checklist unchecked** (`docs/releasing.md`): Trusted Publishing,
   `pypi` environment, branch/tag rulesets, PVR, secret scanning, Dependency Graph/code
   scanning are owner-side actions not yet confirmed; first PyPI release not yet performed.
   (Lot 5)

---

## 6. Corrections to the external audit report

The external report was accurate; only two statements needed refinement:

1. *"No constraints file for the build"* — `constraints/release.txt` and
   `constraints/test.txt` exist; they simply do not constrain the isolated PEP 517 backend, so
   the risk described is real but the statement of fact was incomplete.
2. *"Descriptions still promise the four datasets"* — the phrasing exists
   (`pyproject.toml:8`, `__init__.py`, `cli.py`) but reads as describing FOCUS 1.4's structure;
   it is an alignment issue with the README's careful positioning, not a reinstated false
   promise. No French wording ("quatre datasets") appears anywhere.

---

## 7. Go / No-Go summary

| Use | Verdict |
|---|---|
| Synthetic 1.2/1.3 data generation (demos, training, testing) | **GO** |
| Strict migration, FOCUS 1.3 Cost and Usage → 1.4 | **GO conditional** (sample validation, no official-conformance claim) |
| Strict migration, FOCUS 1.2 → 1.4 | **NO-GO** until §3.1 is fixed (PR-1) |
| Producing all four 1.4 datasets factually from client data | **Not possible today** — requires the supplemental-data feature (Lot 2) |
| Large client CSV processing | **GO conditional** (bundle validation not streaming, not a gate) |
| Client Parquet input | **Not implemented** (PR-10) |
| Official FOCUS 1.4 conformance validation | **Not available and correctly not claimed** |
| 0.9.x beta publication | **Code Ready** — GO after dry run + admin settings |
| 1.0.0 stable | **Premature** — see improvement plan Lots 1–5 |

The companion document
[`2026-07-improvement-plan.md`](./2026-07-improvement-plan.md) turns every finding above into a
sequenced backlog of 18 pull requests in 5 lots (revised 2026-07-17: Lot 2 gained
provider-native supplement adapters for AWS/Azure/GCP exports).
