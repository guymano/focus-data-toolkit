# Compatibility matrix

## Python

| Component                                   | Python support |
| ------------------------------------------- | -------------- |
| Core toolkit (generate, convert, lint, CLI) | **3.11+**      |
| `[parquet]` / `[scale]` extras (PyArrow)    | 3.11+          |
| `[validator]` extra (`focus-validator`)     | **3.12+**      |

The core is standard-library only. The optional FinOps `focus-validator` (the
official validator) requires Python ≥ 3.12; its dependency marker makes
`pip install focus-data-toolkit[validator]` a **no-op on 3.11** rather than an
error.

### The `all` extra under Python 3.11

`focus-data-toolkit[all]` aggregates `parquet` + `validator`. Because
`validator` carries a `python_version >= "3.12"` marker, on **Python 3.11**
installing `[all]` gives you Parquet support but **not** the official validator
(this is intentional — no artificial install failure). For the full
distribution, use Python 3.12+.

## Operating systems

| Capability                                             | Linux | macOS | Windows |
| ------------------------------------------------------ | :---: | :---: | :-----: |
| Generators (FOCUS 1.2 / 1.3 sample data)               |  ✅   |  ✅   |   ✅    |
| Eager (in-memory) conversion to FOCUS 1.4              |  ✅   |  ✅   |   ✅    |
| Schema detection, linting, cross-dataset validation    |  ✅   |  ✅   |   ✅    |
| CLI `generate` / `validate` / eager `convert`          |  ✅   |  ✅   |   ✅    |
| Parquet **write** (with `tzdata` on Windows)           |  ✅   |  ✅   |   ✅    |
| Bounded-memory **streaming** conversion (`--stream`)   |  ✅   |  ✅   |   ❌    |
| Streaming **Parquet** / partitioned output             |  ✅   |  ✅   |   ❌    |

### Known Windows limitation (streaming)

The bounded-memory streaming engine manages many low-level file handles and
publishes results with a POSIX directory `fsync` + atomic `rename`; the
path-traversal guard also assumes POSIX separators. On Windows these raise
`OSError: Bad file descriptor` and the path guard's assumptions do not hold, so
the streaming path is **not supported on Windows** and the affected tests are
skipped there (see `tests/conftest.py`). Everything else — generation, the eager
in-memory conversion, validation, schema detection, and Parquet *write* (with
`tzdata`) — runs and is tested on Windows.

This is tracked as a "Windows streaming / atomic-write hardening" follow-up. On
Windows, use the eager conversion path; on Linux/macOS, `--stream` gives flat
memory regardless of row count.

> Note: because streaming is unsupported on Windows, the POSIX-only path-traversal
> guard it relies on is not a Windows security control. See
> [docs/security-model.md](security-model.md).

## FOCUS specification support

| FOCUS version | Generate | Convert (target) | Detect |
| ------------- | :------: | :--------------: | :----: |
| 1.2           |   ✅     |        —         |   ✅   |
| 1.3           |   ✅     |        —         |   ✅   |
| 1.4           |   —      |    ✅ (target)   |   ✅   |

The toolkit generates provider-realistic FOCUS **1.2 / 1.3** sample data and
converts a 1.2/1.3 Cost and Usage source toward **FOCUS 1.4** (with the modes,
provenance, and conformance guarantees described in the README). The embedded
FOCUS 1.4 model is a documented, verifiable artifact — see
[docs/model-provenance.md](model-provenance.md).

### FOCUS compatibility policy

- New FOCUS versions are added behind an explicit opt-in; the toolkit does not
  silently reinterpret an existing dataset as a newer FOCUS version.
- The version-detection registry is derived from the committed FOCUS 1.4 model
  plus a removed-columns table, so a file is never misclassified across versions
  (a 1.4 file is not taken for 1.3, etc.).
- Support for a FOCUS version is a **feature** change and is versioned under the
  toolkit's own SemVer policy — see [docs/versioning.md](versioning.md).

## Optional extras

| Extra         | Adds                                                | Notes                                  |
| ------------- | --------------------------------------------------- | -------------------------------------- |
| `parquet`     | PyArrow (+ `tzdata` on Windows)                     | Exact `decimal128` columnar output.    |
| `scale`       | alias of `parquet`                                  | Large-input state uses stdlib sqlite3. |
| `validator`   | `focus-validator` (PyPI)                            | Python 3.12+ only.                     |
| `all`         | `parquet` + `validator`                             | `validator` is 3.12+ (see above).      |
| `release`     | `build`, `twine`                                    | Build/inspect distributions.           |
| `dev`         | `release` + `parquet` + pytest/ruff/mypy/coverage   | Full local dev + CI.                   |
