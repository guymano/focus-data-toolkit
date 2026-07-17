# Contributing to focus-data-toolkit

Thanks for your interest in improving `focus-data-toolkit`! This is a
community-maintained toolkit for generating provider-realistic FOCUS sample
data, converting it toward FOCUS 1.4, and linting/validating it. Contributions —
bug reports, FOCUS-conformance fixes, docs, and features — are welcome.

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).

## Reporting issues

- **Security vulnerabilities:** do **not** open a public issue. Follow
  [SECURITY.md](SECURITY.md) (GitHub Private Vulnerability Reporting).
- **FOCUS conformance / correctness bugs** (wrong column, value, rounding, or
  validation verdict) and **feature requests:** open a normal
  [issue](https://github.com/guymano/focus-data-toolkit/issues) using the
  templates. Please include the toolkit version, Python version, platform, and a
  **synthetic** minimal reproduction.
- **Never include real client/billing data** in an issue, PR, test, or fixture.
  Use the synthetic generators or hand-made minimal fixtures (see "No client
  data" below).

## Development setup

Requires Python 3.11+ (3.12+ to exercise the optional `[validator]` extra).

```bash
git clone https://github.com/guymano/focus-data-toolkit && cd focus-data-toolkit

# Option A — pip
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'          # dev pulls pyarrow + tzdata + release/type tooling

# Option B — uv (matches CI; installs from the committed lock)
uv sync --extra dev
```

## Checks to run before opening a PR

CI runs these across an OS/Python matrix; run them locally first:

```bash
ruff check src tests                       # lint
mypy src/focus_data_toolkit                # types (gate; see pyproject for scoped excludes)
pytest -q                                  # default suite (fast, hermetic)
pytest -m slow                             # large-scale / bounded-memory tests
pytest -m packaging                        # build + install a wheel/sdist (needs [release] extra)

python scripts/check_pinned_actions.py     # every GitHub Action is SHA/digest pinned
python scripts/verify_model_provenance.py  # the embedded FOCUS model matches its provenance
```

A PR is expected to be green on lint, types, and the default test suite, and to
keep test coverage at or above the configured floor.

## Determinism & reproducibility (important)

The synthetic generators and the converter are **deterministic**: for an
identical toolkit version and identical parameters (`provider`, `focus_version`,
`rows`, `seed`, options), the output bytes are stable. Please preserve this:

- Keep generator draws in a fixed order; do not introduce a clock, real RNG, or
  environment-dependent behaviour into generation/conversion.
- Golden snapshots live in `tests/fixtures/golden/`:
  - `compatibility_golden/` — outputs verified correct against the FOCUS spec.
    These are a **byte-for-byte** intra-version contract.
  - `correctness_migration/` — cases with a *known* prior defect: the old output
    is archived for comparison, and the **new** output is the spec-correct one.
- If a change **deliberately** alters correct output (a legitimate FOCUS fix),
  regenerate the affected `compatibility_golden` snapshot **in the same PR**, and
  add a `CHANGELOG.md` entry noting the new byte baseline. Byte identity is only
  promised within an exact version — see [docs/versioning.md](docs/versioning.md).
- If a diff changes a snapshot you did **not** intend to touch, treat it as a
  regression and investigate before updating the fixture.

## Changing the embedded FOCUS model

The FOCUS 1.4 model JSON (`src/focus_data_toolkit/model/focus_1_4_model.json`) is
a generated artifact, not a hand-edited file. To change it:

1. Update the source workbook and/or `tools/extract_focus_1_4_model.py`.
2. Re-run the extractor: `python tools/extract_focus_1_4_model.py <workbook.xlsx>`.
3. Update `src/focus_data_toolkit/model/model_provenance.json` (the
   `output.sha256`, `output.bytes`, and `generator.script_sha256` fields).
4. Run `python scripts/verify_model_provenance.py` until it passes.

See [docs/model-provenance.md](docs/model-provenance.md) for the full process
and the `partial` → `complete` provenance gate.

## No client data

This project must never ship, test against, or accept real cloud-billing data.
All fixtures and generated data are synthetic. A test
(`tests/test_fixtures_are_synthetic.py`) scans committed fixtures for patterns
that look like real account identifiers or PII; keep it green.

## Pull requests

- Branch from `main`, keep PRs focused, and open them as **draft** until ready.
- Add or update tests for behaviour changes.
- Update `CHANGELOG.md` under `## [Unreleased]` (Keep a Changelog format).
- PRs touching workflows, packaging, the FOCUS model, or security surfaces
  request a review from the code owner (see [CODEOWNERS](.github/CODEOWNERS)).
- Write clear, imperative commit messages explaining the *why*.

## Code of conduct

Please be respectful and constructive. Harassment or discrimination of any kind
is not tolerated. Maintainers may remove comments, commits, or contributions
that violate this expectation.
