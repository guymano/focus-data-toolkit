# Releasing

This describes how a `focus-data-toolkit` release is cut, and — importantly —
separates what the code/CI can guarantee ("Code Ready") from the **operational,
owner-only actions** that must happen on GitHub and PyPI ("Operationally Ready").

The release **pipeline** lives in `.github/workflows/`:

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `release-build.yml` | `workflow_call` (reusable) | Build wheel+sdist **once** (locked toolchain, `SOURCE_DATE_EPOCH` = commit date), test them, generate the CycloneDX SBOM + `SHA256SUMS` + a build manifest, run `verify_release.py`, upload the artifacts. No publish scopes. |
| `release-dry-run.yml` | `workflow_dispatch` | Calls `release-build` and re-verifies — **no** id-token, **no** environment, publishes nothing. Rehearse on any branch. |
| `release.yml` | push tag `v*` | Calls `release-build`, then **attests** wheel/sdist/SBOM/checksums (GitHub Artifact Attestations, keyless OIDC) and **publishes** to PyPI via Trusted Publishing in the `pypi` environment. The same artifacts flow by digest — nothing is rebuilt to publish. |
| `reproducibility.yml` | `workflow_dispatch` | Double-builds and compares (see Reproducibility below). |

Dry-run vs real release is a **structural** separation (distinct workflows,
scopes granted only where needed), not a boolean flag — a dry-run cannot become
a publish.

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

## The security workflows

`.github/workflows/codeql.yml` and `scorecard.yml` run on `push` / `pull_request`
/ `schedule` (plus `branch_protection_rule` for Scorecard). They upload results
to GitHub **code scanning**, which must be enabled in the repository settings
(Operationally Ready checklist above) for the uploads to land.
`actions/dependency-review-action` similarly needs the **Dependency Graph**; a
`dependency-review` job can be added to `security.yml` once it is enabled
(dependency CVEs are already covered by `pip-audit` + Dependabot in the
meantime).

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
   To complete it, run `python scripts/verify_model_provenance.py --complete
   /path/to/focus_1_4_data_model.xlsx` with the source workbook (needs
   `openpyxl`): it archives the workbook hash and flips the status only after
   reproducing the committed model byte-for-byte.
5. Run the **`release-dry-run.yml`** workflow (build + test + SBOM + checksums +
   `verify_release.py`) — no publish, no PyPI environment.
6. Open the release PR, get code-owner review, merge.
7. Create the protected, signed `vX.Y.Z` tag. **`release.yml`** builds the
   artifacts **once** with the hash-locked backend
   (`constraints/build-backend.txt`, `--no-isolation` so the pinned setuptools
   is the one that builds), tests them, generates **both SBOM profiles**
   (`sbom.cdx.json` declared ranges; `sbom.resolved.cdx.json` exact versions,
   hashed distributions and the transitive tree from `uv.lock`), attests every
   asset (dists, both SBOMs, checksums, build manifest, and the three
   provenance manifests — model / official JSON schemas / provider adapters),
   publishes to PyPI via Trusted Publishing after environment approval, and —
   **only after publish succeeds** — creates the **GitHub Release** on the tag
   carrying the full attested asset set permanently (workflow artifacts expire
   after 7 days) with notes taken from the `CHANGELOG.md` section. Two honesty
   gates enforce the provenance policy mechanically: the publish job **refuses
   a final (non-PEP-440-pre-release) version while model provenance is
   `partial`**, and the GitHub Release is marked **pre-release** with the
   limitation stated in the notes whenever provenance is not `complete`.
   Artifacts pass between jobs **by digest**; nothing downstream rebuilds.
   (The publish job also checks the tag matches the built version.)
8. Verify the published artifacts and that the PyPI page shows the attestations:

   ```bash
   python scripts/verify_release.py --dist dist          # offline: checksums, SBOM, versions
   gh attestation verify focus_data_toolkit-X.Y.Z-py3-none-any.whl \
     --repo guymano/focus-data-toolkit                    # GitHub Artifact Attestation
   ```

## Reproducibility of releases

The pipeline records its runner, Python, build tooling, lock and
`SOURCE_DATE_EPOCH` (commit date) in `dist/release-manifest.json`, so a build can
be reproduced. `reproducibility.yml` double-builds and compares:

- **The wheel is byte-for-byte reproducible** with a fixed `SOURCE_DATE_EPOCH` and
  the same toolchain — asserted as a hard gate.
- **The sdist's file contents are reproducible**, but its `.tar.gz` carries a
  high-precision `mtime` in the tar **PAX extended header** that
  `SOURCE_DATE_EPOCH` does not normalize, so the raw sdist bytes can differ
  between builds. This is a known setuptools/tar limitation; the extracted
  contents are identical. The check reports (does not fail) on the sdist.

We claim byte reproducibility only where the double-build demonstrates it, and
document the limitation rather than asserting a blanket guarantee.
