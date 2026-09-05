# Historical synthetic examples

`pr67_before.json` is a documentation-only archive of representative synthetic
rows from toolkit commit `d0c27bac6d1909fa39fe45538fce3a9d391ae0f8`, before the
0.13.0 corrections. No automated test compares against this archive: the old
values include known defects and must not become expected new output.

Executable expectations live in the corrected `../compatibility_golden/` CSVs,
the independent semantic audit and the regression tests. The source commit in
the archive identifies how to reproduce the historical behavior for inspection.
