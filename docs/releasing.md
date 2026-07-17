# Releasing

This describes how a `focus-data-toolkit` release is cut, and — importantly —
separates what the code/CI can guarantee ("Code Ready") from the **operational,
owner-only actions** that must happen on GitHub and PyPI ("Operationally Ready").
The release **pipeline** itself (reusable build workflow, SBOM, attestations,
dry-run vs real publish) is delivered separately; this document is the process
and the checklist it plugs into.

## Definition of Done — two levels

### Code Ready (verifiable in this repository)

- Lint, types, and the default test suite are green across the OS/Python matrix.
- Coverage is at or above the configured floor.
- `python scripts/check_pinned_actions.py` and
  `python scripts/verify_model_provenance.py` pass.
- Packaging tests build and install a wheel **and** sdist and smoke-test them.
- `CHANGELOG.md` has the release section; `docs/*` are updated.
- No overclaiming: SBOM/attestations are described only where the pipeline
  actually produces them.

### Operationally Ready (owner-only; cannot be done from CI)

These require repository-admin / PyPI-owner rights and are **not** something the
toolkit or an automated agent can perform. A release is only truly done once
these are in place:

- [ ] **PyPI project + Trusted Publishing (OIDC)** configured for this repo and
      the release workflow's environment — no long-lived API tokens.
- [ ] **GitHub Environment** (e.g. `pypi`) created with required reviewers, so a
      publish needs human approval.
- [ ] **Branch protection** on `main` (require PR, required checks, review from
      code owners).
- [ ] **Tag protection / ruleset** for `v*` tags (immutable; signed / `Verified`).
- [ ] **Private Vulnerability Reporting** enabled (Security tab) — see
      [SECURITY.md](../SECURITY.md).
- [ ] **Secret scanning + push protection** enabled.
- [ ] **Dependency Graph + code scanning** enabled, so CodeQL, OpenSSF Scorecard,
      and dependency-review can run (see below).
- [ ] **CODEOWNERS** confirmed and "require review from Code Owners" turned on.

## Enabling the gated security workflows

`.github/workflows/codeql.yml` and `scorecard.yml` are currently gated behind
`workflow_dispatch` only. They upload results to GitHub **code scanning**, which
requires the repository to be public (or to have GitHub Advanced Security).
`actions/dependency-review-action` similarly needs the **Dependency Graph**.

Once the repository is public with code scanning / Dependency Graph enabled
(Operationally Ready checklist above):

1. In `codeql.yml` and `scorecard.yml`, restore the real triggers (`push` /
   `pull_request` / `schedule` / `branch_protection_rule`) that are commented out.
2. Optionally re-add a `dependency-review` job to `security.yml` (dependency CVEs
   are already covered by `pip-audit` + Dependabot in the meantime).

## Cutting a release

1. Ensure **Code Ready** (above) is green on `main`.
2. Bump `src/focus_data_toolkit/_version.py` to the release version.
3. Update `CHANGELOG.md`: move `[Unreleased]` entries under the new dated
   version heading, and confirm the date matches the tag day.
4. Verify the model provenance gate: `python scripts/verify_model_provenance.py`.
   A release presenting fully reproducible model provenance requires
   `provenance_status = "complete"` (see
   [docs/model-provenance.md](model-provenance.md)); otherwise it is a
   pre-release with `partial` provenance and must be described as such.
5. Run the **release dry-run** (build + test + SBOM + checksums + attestation
   rehearsal) — no publish, no PyPI environment.
6. Open the release PR, get code-owner review, merge.
7. Create the protected, signed `vX.Y.Z` tag. The release workflow builds the
   artifacts **once**, tests them, generates the SBOM, attests
   wheel/sdist/SBOM/checksums, and publishes to PyPI via Trusted Publishing
   after environment approval. Artifacts pass between jobs **by digest**; the
   publish job never rebuilds.
8. Verify the published artifacts (e.g. `gh attestation verify`) and that the
   PyPI page shows the attestations.

## Reproducibility of releases

The release pipeline documents its runner, Python, build backend, lock, and
`SOURCE_DATE_EPOCH` so a build can be reproduced. We claim byte reproducibility
only where a double-build check demonstrates it, and we document any limitation
rather than asserting a blanket guarantee.
