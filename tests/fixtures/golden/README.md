# Generator golden fixtures

Byte-for-byte snapshots of generator output, used by `tests/test_generator_golden.py`.

## `compatibility_golden/`

Output that was **reviewed as correct against the FOCUS spec** and is therefore a stable
byte-for-byte contract. Captured from the pre-refactor per-provider generators; the shared
generation engine (P2-A) reproduces every file exactly.

Coverage: each provider (`aws`, `azure`, `gcp`) × each version (`1.2`, `1.3`) for the Cost and
Usage dataset over a small `(rows, seed)` grid (including an `include_credits` variant), the
1.3 Contract Commitment dataset, and the provider-agnostic coherent scenarios
(`split_allocation_group`, `correction_set`) that share the single-source SCA JSON builder.

**Changing a file here is a deliberate reproducibility break.** Synthetic output is
byte-reproducible for an *identical toolkit version* (see `docs/versioning.md`, added in P2-D).
A legitimate change (e.g. a new service, a FOCUS-conformance fix) requires:

1. regenerating the affected fixture(s),
2. a `CHANGELOG.md` entry describing the output change, and
3. justification that the new bytes are correct.

## `correctness_migration/`

Reserved for cases where the *old* output was found to be incorrect against the FOCUS spec and
was deliberately changed: the old bytes are archived for comparison and the new expected bytes
are spec-based, with the break documented in the changelog.

The P2-A correctness review of the current generators found **no incorrect output** — in
particular the two historical Split Cost Allocation JSON builders (the 1.3 generators and
`scenarios.py`) were verified to be byte-identical on all valid inputs, so their reconciliation
onto `engine/json_focus.allocated_method_details` is a *compatibility* change, not a migration.
This directory is therefore intentionally empty.
