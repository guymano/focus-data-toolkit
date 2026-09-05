# PR 53: verified review follow-up

Review examined: Claude Code review published on PR 53 at head `92fa0fa`.
The review found no blocking defect in the generated cost arithmetic. We checked
all 20 inline comments and the three additional suggestions against the code and
the approved migration plan. Recommendations were not accepted automatically.

| Comment | Finding and disposition |
|---|---|
| [SKU/currency coupling](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940287233) | Confirmed. Currency conversion no longer changes identity; builders finalize SKU identity explicitly, and missing/invalid details have actionable errors. Removed the unused committed-resource-name hook. The SKU hooks still consume RNG draws, so they are not behaviorally dead: those draws are retained and documented to preserve the reviewed byte baseline. |
| [Dead dispatch entries](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940287379) | Confirmed. Removed unreachable tax/commitment entries and corrected the dispatch/RNG documentation. |
| [Taxes on allocated shares](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940287605) | Reproduced exactly: AWS 21/41, Azure 34/59, GCP 30/42 tax rows reference allocated shares. This is not a demonstrated defect: the approved source pool is Standard Usage. Kept the behavior and documented/tested that each tax applies to the referenced share, without becoming a member of the source allocation group. |
| [Subscription bounds](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940287709) | Confirmed readability issue. Documented the inclusive 20.00–800.00 USD hash-derived range. |
| [Alternate contract-ID fallback](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940287832) | Confirmed. Commitment builders now require the common registry; removed the alternate ID helper and updated direct builder tests. |
| [Numeric fleet-size tag](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940287958) | No demonstrated defect. Number 500 deliberately represents a synthetic measurement. Documented the choice; retained existing bytes and numeric audit assertion. |
| [Repeated commitment ID](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940288135) | Confirmed by direct reproduction. Duplicate registration now raises before changing counters or mappings; a regression test verifies the registry is unchanged. Added the public re-export and moved the import to module scope. |
| [ARN namespace placement](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940288317) | Design preference, not a reproduced bug. All current service namespaces are covered and independently audited. Moving an entry to an unvalidated dictionary would not by itself provide construction-time validation. Kept the current explicit mapping. |
| [CSV encoder duplication](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940288465) | Confirmed. One typed encoder now serves all paths; profile/adapter types are explicit. Bundle generation accepts the same credit option and is compared against individual APIs with credits both on and off. |
| [Public official-validator behavior](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940288687) | Confirmed compatibility/documentation gap. Conflicting output modes or destinations are rejected before launch; untested installed versions warn; incomplete reports explain the required format. Buffered output is documented in help and the migration guide. In 2.2.1 the actual alternate modes are `unittest`/`web`; `json` was already unsupported upstream. |
| [Real console-log test](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940288875) | Confirmed gap in the default unit suite. It now parses an actual archived 114-rule Contract Commitment report and asserts all four totals. |
| [Source manifest cost](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940289069) | The operational cost is real, but downgrading it contradicts the approved strict source-provenance requirement. Kept the gate and added a list of changed files to the error. Fixture CSV hashes alone do not identify the generator/audit code or cover every possible option. New source evidence was regenerated and reviewed. |
| [Temporary comparison files](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940289160) | Confirmed. Successful live comparisons remove their generated temporary directory; failed runs and explicit candidates retain diagnostic files. |
| [Commit-bound Gitleaks exclusion](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940289301) | Confirmed. Replaced it with a default-extending rule allowlist requiring both the exact baseline path and the complete source-hash line. Tested with Gitleaks 8.24.3: historical PR commits pass, while three negative controls still flag a different key in that file, the same line elsewhere, and a changed value. No whole-file or general hash exemption. |
| [Release comparison link](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940289390) | Confirmed. The dated entry now targets `v0.12.0...v0.13.0`; the tag remains a later release action. |
| [Repeated quantization](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940289531) | Confirmed. Compute the lossless display candidate once. Kept the converter independent of generator internals; exact output remains unchanged. |
| [Contract test population](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940289682) | Confirmed. Both fresh generated variants use 1,000 rows. Term-population coverage is a separate test; the three 100-row goldens explicitly skip that population test while continuing to run all referential/metric checks. |
| [Exit-code test name](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940289802) | Confirmed. Nonzero passthrough is separate from the existing zero-exit/empty-report regression. |
| [Implicit scripts namespace](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940289959) | Confirmed import ambiguity. Added `scripts/__init__.py`; importing the audit modules no longer changes `sys.path`. A fresh-process test verifies this. |
| [Independent fee constants](https://github.com/guymano/focus-data-toolkit/pull/53#discussion_r3940290104) | Confirmed documentation gap. Named the actual formula: provider `_SERVICES[0].unit_price_usd × COMMIT_RATE × 500`. The audit retains independent literal expectations. |

Additional suggestions from the review body:

- **Empty stderr logs:** now meaningful assertions. Both live capture and offline
  verification require empty stderr; a corrupted archive is rejected by a test.
- **Historical `pr67_before.json`:** a directory README now explicitly identifies
  it as a documentation-only archive, not an executable expected-output fixture.
- **Verbose `baseline.json`:** retained. Full model expressions, minimal source
  records and rule inventories provide the requested self-contained evidence.
  Reducing it is a possible format optimization, not a correctness repair.

These changes preserve generated CSV bytes and the documented statistics. Source
fingerprints and the captured evidence are refreshed because provenance remains
strict. Golden tests check that the cleanup does not silently establish another
data baseline.

Validation of the review changes:

| Check | Result |
|---|---|
| Windows, Python 3.12, full default suite | 1,121 passed, 28 skipped; 89.93% coverage |
| Linux, Python 3.12, full default suite | 1,146 passed, 3 skipped; 90.20% coverage |
| Conformance suite after the test cleanup | 364 passed, 3 explicit population skips |
| Ruff / mypy | Passed; 91 source files checked by mypy |
| Fresh official candidate vs approved reference | All nine CSV hashes, statistics, rule inventories, statuses, violation counts and independent failure evidence unchanged |
| Promoted archive, offline verification | Nine complete reports and source/resource provenance verified |
| Gitleaks 8.24.3 | PR history passes; all three negative controls remain detected |

Default-suite skips include three explicit population checks for small golden
fixtures; Windows also skips 25 platform-specific cases. Packaging and the slow
memory test are separate suites. Passing the non-regression comparison does not
turn known official FAIL or SKIPPED rules into conformance passes.
