# Supplemental client data

A FOCUS 1.2/1.3 Cost and Usage source cannot factually populate every column the four
FOCUS 1.4 datasets require: Billing Period cycle facts, invoice-header and invoice-line
facts, and the 1.4-new Contract Commitment commercial terms are provider-issued and simply
absent from older exports. Strict mode therefore refuses to produce those datasets from a
Cost and Usage source alone.

**Supplements** close that gap: the client supplies the missing facts, the toolkit joins
them to the conversion on natural FOCUS keys, records them with `ENRICHED` lineage and
full attribution, and — when coverage is complete — strict mode produces all four
datasets with nothing invented.

## Step 1 — find out exactly what is missing: `gaps`

```console
$ focus-toolkit gaps --cost-and-usage my_focus_1_2.csv
$ focus-toolkit gaps --cost-and-usage my_focus_1_3.csv --contract-commitment cc_1_3.csv \
      --format json --out gaps.json
```

The report is **computed from the converter's own provenance rules** (a blocking gap is
exactly what `strict_blockers` would refuse), annotated from the embedded FOCUS 1.4 model
(allowed values, formats, conditions), and maps every blocking column to the supplement
kind that satisfies it. The JSON output includes a ready-to-fill CSV header per kind.

Cost and Usage gaps are different: they mean the *source itself* is missing mandatory
FOCUS 1.x columns. No supplement can fabricate those — fix the export instead.

## Supplement kinds

| Kind | Target dataset | Join keys | Fact columns |
|---|---|---|---|
| `billing_period` | Billing Period | `InvoiceIssuerName`, `BillingPeriodStart`, `BillingPeriodEnd` | `BillingPeriodCreated`, `BillingPeriodLastUpdated`, `BillingPeriodStatus` |
| `invoice` | Invoice Detail (header level) | `InvoiceIssuerName`, `InvoiceId` | `InvoiceIssueDate`, `InvoiceIssueStatus`, `PaymentTerms`, `PaymentDueDate`, `ReferenceInvoiceId`, `PurchaseOrderNumber`, `PaymentCurrency` |
| `invoice_line` | Invoice Detail (line level) | the full business grain (`InvoiceIssuerName`, `InvoiceId`, `BillingAccountId`, `BillingCurrency`, `BillingPeriodStart`, `BillingPeriodEnd`, `ChargeCategory`) | `InvoiceDetailId`, `InvoiceDetailCreated`, `InvoiceDetailLastUpdated`, `InvoiceDetailDescription`, `InvoiceDetailGrain`, `PaymentCurrencyBilledCost`, `PaymentCurrencyInvoiceDetailId` (+ `BilledCost`, accepted only as a reconciliation check) |
| `contract_commitment` | Contract Commitment | `ContractCommitmentId` | the 1.4-new commercial terms (`ContractCommitmentCreated/LastUpdated`, `ContractCommitmentApplicability`, `…BenefitCategory`, `…FulfillmentInterval`, `…LifecycleStatus`, `…Model`, `…OfferCategory`, `…PaymentInterval`, `…PaymentModel`, `…PaymentUpfrontPercentage`, `…DiscountPercentage`) plus optional `ServiceProviderName` / `InvoiceIssuerName` overrides |

Rules that apply to every kind:

- Column names are FOCUS column names (the `gaps` templates give them to you); any extra
  column must be `x_`-prefixed.
- Join-key values are compared after whitespace stripping — never fuzzily. A mismatch is
  a diagnostic, not a guess.
- Supplied values are validated against the embedded FOCUS 1.4 model (types, formats,
  allowed values) and cross-checked against the source before use.

## Step 2 — pre-flight check (optional but recommended)

```console
$ focus-toolkit supplements validate --cost-and-usage my_focus_1_2.csv \
      --supplement invoices.csv --supplement billing_periods.csv
```

Reports every `FDT-SUPP-0xx` diagnostic (duplicate keys, unknown columns, values outside
the model's formats/allowed values, orphan rows, `BilledCost` reconciliation conflicts,
and per-column coverage) without converting anything. Exit 1 on any ERROR.

## Step 3 — convert with supplements

```console
$ focus-toolkit convert --cost-and-usage my_focus_1_2.csv \
      --supplement billing_periods.csv --supplement invoices.csv \
      --supplement lines.csv:invoice_line --out focus-1.4 --mode strict

# or with a bundle directory (its supplements.json carries per-file provenance/as_of):
$ focus-toolkit convert --cost-and-usage my_focus_1_2.csv \
      --supplements-dir ./supplements --out focus-1.4 --stream
```

Behavior (identical in the eager and streaming paths — outputs are byte-identical):

- The bundle is validated against the exact source **before anything is staged**; any
  ERROR refuses the conversion.
- Supplied facts are applied with **`ENRICHED` lineage** and full attribution; the
  manifest gains a `supplements` section (kind, sha256, row counts, declared provenance)
  and per-value `lineage_summary` counters.
- **Strict mode**: a non-nullable column becomes factual only at 100 % key coverage —
  at full coverage all four FOCUS 1.4 datasets are `PRODUCED` with nothing invented
  (exit 0); partial coverage leaves the dataset `NOT_PRODUCED` with the remaining
  `blocking_columns` and `FDT-SUPP-010` coverage counts telling you exactly what is
  missing. Uncovered nullable assumed columns are emitted empty (synthetic defaults
  never leak into a strict output). Real issuer-assigned `InvoiceDetailId`s replace the
  locally generated `x_fdt_idl_v1_*` back-links.
- **Synthetic mode**: supplied values win, documented defaults fill the rest, and the
  counters record the enriched/assumed mix per column.

## Provider-native exports (AWS / Azure / GCP) — no manual renaming

If your supplemental data is a **native provider export**, an adapter recognizes its format
by its own field names and translates it into the canonical kinds above — you do not rename
anything by hand. List the available adapters:

```console
$ focus-toolkit supplements adapters
aws-invoice-summary (v1) -> invoice
    source: AWS Invoicing API — InvoiceSummary (ListInvoiceSummaries)
    doc:    https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_invoicing_InvoiceSummary.html
aws-savings-plans (v1) -> contract_commitment
    ...
```

Then just pass the export straight to `convert` / `supplements validate`:

```console
$ focus-toolkit convert --cost-and-usage my_focus_1_2.csv \
      --supplement aws_invoices.json --supplement aws_savings_plans.csv \
      --supplement payment_terms.csv --out focus-1.4 --mode strict
```

The adapter is auto-detected from the header; force one with `FILE:<adapter-name>` (e.g.
`aws_export.json:aws-invoice-summary`). Each adapter is a **vendored, versioned mapping
table** carrying its official-doc provenance (see
`focus_data_toolkit/supplement/adapters/adapters_provenance.json`); translated values keep
`ENRICHED` lineage with the attribution `supplement:<adapter>@<version>:<file>`, so every
value is auditable back to the native export. Honesty rules:

- An adapter only maps fields its table describes. Facts the export does not contain (e.g.
  AWS does not expose payment terms on the invoice summary, or a savings-plan's
  applicability scope) are **not** emitted — the coverage report shows the residual gap and
  you supply those separately.
- An export that matches no adapter falls back to the generic FOCUS-named path with a clear
  message. Nothing is ever guessed.
- Account-type / format variants are distinct versioned tables; an unrecognized variant
  falls back rather than half-matching.
