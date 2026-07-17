# Security model

This describes the toolkit's design-level security posture and its supply-chain
controls. It is a description of intent and current controls, **not** a
certification. For how to *report* a vulnerability, see
[SECURITY.md](../SECURITY.md).

## What the toolkit does

`focus-data-toolkit` is a local, offline command-line and library tool that:

- generates synthetic FOCUS sample data,
- converts a FOCUS 1.2/1.3 Cost and Usage file toward FOCUS 1.4,
- lints/validates FOCUS datasets.

The **core performs no network I/O by design** and has no runtime dependencies
beyond the Python standard library. It reads local input files and writes local
output files deterministically. There is no telemetry, no phone-home, and no
credential handling in the core.

## Data handling

The toolkit may process **confidential cost and billing data**. Its data-handling
guarantees:

- Input is read locally and never transmitted anywhere.
- Output is written only to the user-specified directory, published with an
  atomic rename so partial/failed runs do not leave half-written results.
- An operational sidecar (`_run.json`) holds run id / timestamp / per-file
  hashes and is kept **out** of the business manifest so dataset bytes remain
  reproducible.

**Do not** put real client data into issues, PRs, tests, or fixtures — use the
synthetic generators (enforced by `tests/test_fixtures_are_synthetic.py`).

## Trust boundaries & attack surface

The main untrusted input is the **files** the tool parses:

- **CSV / gzip** parsing: field-count validation with line-numbered errors;
  gzip auto-detection on input.
- **Parquet** (optional `[parquet]` extra): used for local columnar *output* and
  read-back lint; the toolkit does **not** deserialize untrusted remote Arrow-IPC
  streams.
- **Path handling**: outputs go through an atomic writer. On POSIX a
  path-traversal guard rejects names that escape the output root.

### Platform note (Windows streaming)

The bounded-memory streaming path and its POSIX path-traversal guard are **not
supported on Windows** (see [docs/compatibility.md](compatibility.md)). Treat the
traversal guard as a POSIX control; on Windows, use the eager conversion path.

## Dependencies & known exceptions

Runtime dependencies are minimized:

- **Core**: standard library only.
- **Optional**: PyArrow (`parquet`), `focus-validator` (`validator`, 3.12+),
  `tzdata` (Windows, for Parquet timezone resolution).

Dependency vulnerabilities are addressed **or documented**:

- **PYSEC-2026-113 (pyarrow < 23.0.1)** is currently accepted as a documented
  exception in the `pip-audit` gate (`.github/workflows/security.yml`). PyArrow
  is an *optional* extra used only for local `decimal128` columnar output — the
  toolkit does not perform untrusted Arrow-IPC/Parquet deserialization of remote
  data, and the fix is outside our tested `>=15,<21` range. It is tracked for a
  pyarrow-cap bump.

## Supply-chain controls

These reduce risk; they are **indicators, not guarantees**:

- **Pinned actions**: every GitHub Action is pinned to a full commit SHA (and
  Docker image actions to an `@sha256` digest), enforced by
  `scripts/check_pinned_actions.py` and a test.
- **Least privilege**: workflows declare a top-level `permissions: contents:
  read`; jobs elevate only the scopes they need.
- **Locked dependencies**: a committed `uv.lock` plus hash-pinned
  `constraints/*.txt` for reproducible installs.
- **Automated scanning**: `pip-audit` (dependency CVEs), `gitleaks` (secrets),
  `actionlint` + `zizmor` (workflow security), Dependabot (updates), and — once
  code scanning is enabled on the repository — CodeQL and OpenSSF Scorecard.
- **Provenance**: the embedded FOCUS model is verifiable
  (`scripts/verify_model_provenance.py`); the release pipeline (P2-E) adds SBOM
  and build attestations.

> OpenSSF Scorecard is a **posture indicator**, and a green pipeline is a signal,
> not proof of security. We deliberately avoid overclaiming SLSA levels or
> "reproducible/secure by certification"; we claim only what the tooling
> demonstrates.

## Enabling repository-level scanning (operational)

CodeQL, Scorecard, and dependency-review require GitHub code scanning /
Dependency Graph, which is available on public repositories (or with GitHub
Advanced Security on private ones). Until that is enabled, those workflows are
gated behind `workflow_dispatch` so they do not fail. The operational steps are
in [docs/releasing.md](releasing.md).
