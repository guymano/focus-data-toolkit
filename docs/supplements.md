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

> **Provider-native exports (AWS / Azure / GCP):** adapters that translate documented
> provider export formats (invoice summaries, reservation/commitment inventories) into
> these kinds automatically are part of this feature set — see the improvement plan
> (Lot 2, PR-9a/PR-9b). With an adapter, you do not rename anything by hand.
