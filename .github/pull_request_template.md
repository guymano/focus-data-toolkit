<!--
Thanks for contributing! Keep PRs focused. For a security fix, coordinate first
via SECURITY.md (private reporting) rather than opening a public PR.
-->

## Summary

<!-- What does this PR change, and why? -->

## Type of change

- [ ] Bug fix (non-breaking)
- [ ] FOCUS conformance / correctness fix (may change output bytes)
- [ ] New feature (non-breaking)
- [ ] Breaking change (public API / CLI / output layout)
- [ ] Docs / CI / tooling only

## Checklist

- [ ] Lint, types, and the default test suite pass locally
      (`ruff check src tests`, `mypy src/focus_data_toolkit`, `pytest -q`).
- [ ] Tests added/updated for the change.
- [ ] `CHANGELOG.md` updated under `## [Unreleased]`.
- [ ] Docs updated if behaviour, compatibility, or the security model changed.
- [ ] No real client/billing data added to code, tests, or fixtures (synthetic only).

## If generator/converter output changed

- [ ] The change is a deliberate, spec-justified correctness fix.
- [ ] Affected golden snapshots regenerated in this PR, with a `CHANGELOG.md` note
      about the new byte baseline (see `docs/versioning.md`).

## If the embedded FOCUS model changed

- [ ] Regenerated via `tools/extract_focus_1_4_model.py` (not hand-edited).
- [ ] `model_provenance.json` hashes updated and
      `python scripts/verify_model_provenance.py` passes.
