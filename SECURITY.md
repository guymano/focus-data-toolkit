# Security Policy

`focus-data-toolkit` is a community-maintained, best-effort open-source project.
We take security seriously and will respond to reports as promptly as we can,
but there is no commercial support contract or guaranteed response SLA.

## Reporting a vulnerability

**Please report suspected vulnerabilities privately — do not open a public
issue, PR, or discussion for a security problem.**

Use **GitHub Private Vulnerability Reporting**:

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability** ("Privately report a vulnerability").
3. Describe the issue, the affected version, and a minimal reproduction.

This keeps the report private to the maintainers until a fix is available. If
Private Vulnerability Reporting is not visible, open a **minimal** public issue
that says only "requesting a private security contact" (no details) and wait for
a maintainer to open a private channel.

### What to include

- The affected version (`python -c "import focus_data_toolkit as f; print(f.__version__)"`).
- The platform and Python version.
- A **minimal, synthetic** reproduction (see "No client data" below).
- The impact you observed or believe is possible.

### Do NOT include client data

This toolkit processes cost and billing data that may be confidential. When
reporting, **never attach real customer, account, billing, or otherwise
sensitive data.** Reproduce the issue with the synthetic generators
(`focus_data_toolkit.generators`) or hand-made minimal fixtures. Reports
containing real client data may be deleted without being actioned.

## Coordinated disclosure

We follow coordinated disclosure on a best-effort basis:

- **Acknowledge** the report within about **7 days**.
- **Assess and true up** severity, and agree an approximate timeline with you.
- **Fix** in a private branch, release a patched version, and publish an
  advisory (GitHub Security Advisory) crediting the reporter unless you prefer
  to remain anonymous.

Please give us a reasonable opportunity to release a fix before any public
disclosure. We do not currently operate a paid bug-bounty program.

## What is — and is not — a security vulnerability

This project is a data-generation / schema-conversion / linting toolkit. Please
route reports accordingly:

**Security vulnerabilities** (report privately, as above) — for example:

- Arbitrary code execution, or code/command injection, from processing an
  input file or CLI arguments.
- Path traversal or writing outside the intended output directory.
- Unsafe deserialization, or a crafted input that causes unbounded memory/CPU
  use (denial of service) beyond documented streaming limits.
- Leakage of secrets, or of input data, to logs, network, or unintended files.
- A supply-chain issue in the release/build pipeline or a published artifact.

**Not security vulnerabilities** (please open a normal
[issue](https://github.com/guymano/focus-data-toolkit/issues) instead):

- **FOCUS conformance or data-correctness bugs** — wrong column, value, rounding,
  or validation verdict. These are correctness defects, tracked as ordinary
  bugs and changelog entries (see `docs/versioning.md`), **not** security issues.
- Feature requests, or disagreements about default behaviour.
- Vulnerabilities in an **optional** third-party dependency (e.g. the
  `[validator]` or `[parquet]` extras) that do not affect the core toolkit —
  report those upstream; we track them via Dependabot and `pip-audit` and will
  bump our supported range when a fix is available.

Note: the core toolkit is standard-library only and performs **no network I/O**
by design; it reads and writes local files deterministically. This intentionally
small attack surface is part of the security model — see
`docs/security-model.md`.

## Supported versions

This project is pre-1.0. Only the most recent minor line receives security
fixes; there are no long-term-support branches yet.

| Version    | Supported                                                   |
| ---------- | ----------------------------------------------------------- |
| `0.11.x`   | ✅ Current line — security fixes land here.                 |
| `< 0.11.0` | ❌ Superseded pre-1.0 releases / snapshots; upgrade to 0.11.x. |

When `1.0.0` is released, this table will be updated with the then-current
support policy.
