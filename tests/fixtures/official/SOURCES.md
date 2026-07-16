# Official FOCUS fixtures — provenance

These fixtures are authored **by hand from the official FOCUS specification** (not
produced by this toolkit's generators), so the tests that consume them are
independent of the code under test. Every value traces to a frozen-tag source in
`FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec`.

| Fixture | Normative source (frozen tag) |
|---|---|
| `numeric_format_examples.json` | `specification/attributes/numeric_format.md` @ `v1.4` — the "Examples" valid/invalid lists. Scientific E-notation `mEn` is permitted; the exponent sign is written only when negative (`35.2E-7` valid, `35.2E+7` invalid); no leading `+`; commas / `^` / fractional notation invalid. |
| `invoice_detail_grain_example.json` | `specification/datasets/invoice_detail/columns/invoicedetailgrain.md` @ `v1.4` — the official Object Example. `Key-Value Format`; FOCUS-defined keys use the Cost-and-Usage Column ID (PascalCase); non-FOCUS keys MUST be `x_`-prefixed. |
| `contract_applied` cases (inline in `tests/test_contract_applied.py`) | `specification/datasets/cost_and_usage/columns/contractapplied.md` @ `v1.3` / `v1.4`. 1.3 identifier keys are `ContractID`/`ContractCommitmentID` (uppercase `ID`); 1.4 re-cases them to `ContractId`/`ContractCommitmentId`. Metric keys `ContractCommitmentApplied{Cost,Quantity,Unit}` are stable; cost/quantity are Numeric (JSON numbers). |
