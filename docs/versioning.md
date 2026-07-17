# Versioning & compatibility policy

## Scheme

`focus-data-toolkit` uses [Semantic Versioning 2.0.0](https://semver.org/) with
[PEP 440](https://peps.python.org/pep-0440/)-compatible version strings
(`MAJOR.MINOR.PATCH`). The version has a single source of truth,
`src/focus_data_toolkit/_version.py`, and is exposed as
`focus_data_toolkit.__version__`.

- **MAJOR** — incompatible public API/CLI changes.
- **MINOR** — backwards-compatible functionality.
- **PATCH** — backwards-compatible fixes.

## Pre-1.0 (current)

The project is in the `0.y.z` range. Per SemVer, the public API should not be
considered stable before `1.0.0`: a **minor** bump may include a breaking change
while we gather feedback from the first public releases. Breaking changes are
still called out explicitly in [CHANGELOG.md](../CHANGELOG.md).

`1.0.0` is intentionally **not** the first published version. It is reserved for
after the toolkit has been on PyPI, has had real-world feedback, and the
deprecated compatibility aliases (e.g. `validate_focus_1_4`) have been removed.
The first public release is `0.9.0` — a deliberate "near-stable" signal.

## What counts as a breaking change

The public surface we version against:

- The importable API re-exported from `focus_data_toolkit/__init__.py`.
- The `focus-toolkit` CLI: subcommands, flags, and documented exit codes.
- The **byte layout** of synthetic generator output and of converted datasets,
  for a given set of parameters (see reproducibility below).

Internal modules (anything not re-exported at the package root) may change at any
time.

## Deprecation policy

Renamed or superseded public names are kept as **deprecated aliases** for at
least one minor release before removal, and the removal is announced in the
changelog. Example: the linter entry point moved to
`lint_focus_1_4_structure`, with `validate_focus_1_4` retained as a deprecated
alias.

## Reproducibility: an exact-version contract

Synthetic outputs are **byte-reproducible for an identical toolkit version** and
identical generation parameters (`provider`, `focus_version`, `rows`, `seed`,
and generation options). Conversion is likewise deterministic for identical
inputs and options.

**Cross-version byte identity is _not_ guaranteed** unless a release explicitly
states it. A legitimate FOCUS-conformance fix can change a generated value in a
`PATCH` or `MINOR` release; when that happens it is documented as a new byte
baseline in the changelog. Users who require absolute byte stability should pin
an exact version (`focus-data-toolkit==X.Y.Z`).

### Golden snapshots

The generator test suite pins this contract with golden snapshots under
`tests/fixtures/golden/`, in two deliberately distinct categories:

- **`compatibility_golden/`** — outputs verified correct against the FOCUS
  specification. These are a **byte-for-byte** intra-version contract: any diff
  is a deliberate change that must be regenerated in the same PR and noted in the
  changelog.
- **`correctness_migration/`** — cases that previously produced a **known
  defect**. The old output is archived for comparison only; the *expected* output
  is the spec-correct one. We preserve *valid* behaviour, never *known errors*.

## FOCUS specification versions

The toolkit's own version is independent of the FOCUS specification versions it
supports (generate 1.2/1.3, convert toward 1.4, detect 1.2/1.3/1.4). The FOCUS
version support matrix and the FOCUS compatibility policy live in
[docs/compatibility.md](compatibility.md).
