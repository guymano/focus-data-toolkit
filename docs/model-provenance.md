# FOCUS model provenance

The toolkit embeds a machine-readable representation of the FinOps **FOCUS 1.4
data model** and uses it for schema detection, conversion targets, and linting.
This document records where that model comes from, how it is produced, how to
verify it, and the honest limits of its reproducibility.

## The artifacts

| File | Role |
| --- | --- |
| `src/focus_data_toolkit/model/focus_1_4_model.json` | The extracted, committed model — **the artifact of record**. |
| `src/focus_data_toolkit/model/focus_1_4_servicesubcategory.json` | ServiceSubcategory → parent Category supplement (an extractor input). |
| `src/focus_data_toolkit/model/model_provenance.json` | Machine-readable provenance manifest (source, license, generator, output hash). |
| `schema/model_provenance.schema.json` | JSON Schema for the manifest (draft 2020-12). |
| `tools/extract_focus_1_4_model.py` | The extractor — the process of record. |
| `scripts/verify_model_provenance.py` | The verifier / CI gate. |

## Source & license

The model is derived from the FinOps Foundation **"FOCUS 1.4 Data Model"**
workbook, published at <https://focus.finops.org>. The FOCUS specification and
its materials are © the FinOps Foundation and are licensed **CC-BY-4.0**
(verified against the FOCUS specification repository's license):

- <https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec/blob/main/license.md>
- <https://creativecommons.org/licenses/by/4.0/>

The committed model JSON and this provenance file are a **derivative work under
CC-BY-4.0 with attribution**. See [NOTICE](../NOTICE). "FOCUS" and "FinOps" are
trademarks of the FinOps Foundation; this project is independent and not
endorsed by the Foundation.

## How the model is produced (reproducibility)

`tools/extract_focus_1_4_model.py` reads the workbook (via `openpyxl`, a
dev-time-only dependency) plus the ServiceSubcategory supplement and writes the
committed JSON with a **deterministic serialization**:

```python
json.dumps(model, indent=2, sort_keys=True) + "\n"   # UTF-8
```

Because the serialization is canonical (sorted keys, fixed indent, trailing
newline), the same `(workbook, supplement, extractor)` reproduces **identical
output bytes**. Neither the runtime package nor the test suite imports
`openpyxl`; the committed JSON is what ships and what is validated.

To regenerate after a workbook or extractor change:

```bash
python tools/extract_focus_1_4_model.py /path/to/focus_1_4_data_model.xlsx
# then update model_provenance.json (output.sha256, output.bytes, generator.script_sha256)
python scripts/verify_model_provenance.py
```

## Provenance status: the `partial` → `complete` gate

`model_provenance.json` carries an explicit `provenance_status`:

- **`partial`** — the source (name, homepage, repository, license) is known and
  the output is reproducible, but the **exact source workbook artifact is not
  archived or hashed** (`source.artifact_sha256` is `null`).
- **`complete`** — additionally, the exact source artifact is identified,
  **hashed** (`source.artifact_sha256`), retrieved-dated, and license-verified,
  so the whole chain is end-to-end verifiable.

The current status is **`partial`**: the FOCUS workbook is not redistributed in
this repository, so we cannot commit and hash the exact source artifact without
overclaiming. This is a deliberate, honest limitation.

> **Release gate.** A stable release that presents the model provenance as fully
> verifiable / end-to-end reproducible **requires `provenance_status = "complete"`.**
> While it is `partial`, the release must not be presented as having fully
> reproducible model provenance. This gate is enforced structurally by the JSON
> Schema (`if provenance_status == "complete"` then a source hash is required)
> and by `scripts/verify_model_provenance.py`.

## What is verifiable today

`scripts/verify_model_provenance.py` runs in the default test suite
(`tests/test_model_provenance.py`) and as a standalone command. It fails on any
mismatch and checks:

- `output.sha256` / `output.bytes` match the committed `focus_1_4_model.json`;
- `generator.script_sha256` matches the committed extractor;
- every `supplements[].sha256` matches its committed file;
- the `partial`/`complete` gate is respected;
- (when `jsonschema` is installed) the manifest validates against the JSON
  Schema. The standalone stdlib checks above run everywhere, with no extra
  dependency.

What is **not** verifiable from this repository alone: the source workbook hash
(hence `partial`). Reproducing the model end-to-end requires obtaining the same
workbook revision from the FinOps Foundation.
