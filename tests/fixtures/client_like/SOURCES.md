# Client-like fixtures — provenance

These fixtures are **hand-authored** consolidated exports, not produced by this toolkit's
own generators, so the tests that consume them exercise the pipeline on data the code under
test did not create (the P0/P1 goal of tests independent of the internal generators).

| Fixture | What it is |
|---|---|
| `consolidated_multi_provider_1_3.csv` | A FOCUS **1.3** Cost and Usage export (full 65-column 1.3 shape) that consolidates **multiple providers** (AWS; Microsoft Azure; a marketplace/reseller row where the service provider *Datadog* differs from the host provider *AWS*), **multiple invoice issuers** (AWS, Microsoft, Reseller X), **two currencies** (USD, EUR) and **multiple billing accounts** — plus a Tax line. Column values are chosen to be structurally and semantically valid so the converted Cost and Usage lints clean; the point is to prove detection, multi-provider grouping and cross-dataset validation on a heterogeneous consolidated file. |
