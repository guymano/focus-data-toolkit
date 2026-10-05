# FOCUS 1.2/1.3 to 1.4 conversion rules

This page states what the converter does to each Cost and Usage value. It also lists the 1.4 rules
it cannot meet from a 1.2 or 1.3 source. Every rule cites the FOCUS specification
([FOCUS_Spec](https://github.com/FinOps-Open-Cost-and-Usage-Spec/FOCUS_Spec), tags `v1.2`
`7296e31`, `v1.3` `c67b95b`, `v1.4` `f1eeb30`) and, where one exists, the rule identifier of the
v1.4 requirements model (`specification/requirements_model/releases/1.4`).

## Copied, renamed and dropped columns

- Every 1.4 column present in the source is copied verbatim. Allowed values did not change between
  1.2 and 1.4 for any enumerated column, so enum values are never rewritten.
- `ProviderName` and `PublisherName` (deprecated in 1.3, removed in 1.4) are dropped. For a 1.2
  source, `ServiceProviderName` and `HostProviderName` are derived from `ProviderName`.
- Columns new in 1.3 or 1.4 that the source does not carry are emitted null, for example
  `CommitmentProgramEligibilityDetails` and `InvoiceDetailId`. The manifest records them as
  `UNAVAILABLE`.

## Values migrated to a 1.4 rule

These changes are deterministic. Each one is counted, reported as a warning in the manifest, and
recorded with `DERIVED` lineage in the per-value `lineage_summary`.

| Code | Rule | What changes |
|---|---|---|
| `FDT-MIG-001` | CAU-EffectiveCost-C-017: `EffectiveCost` MUST equal `BilledCost` when `ChargeCategory` is "Tax". In 1.3 a tax's effective cost followed the related charges' effective cost (rule C-009, removed in 1.4). | On Tax rows, `EffectiveCost` takes the `BilledCost` value. A non-null `PricingCurrencyEffectiveCost` follows it when `PricingCurrency` equals `BillingCurrency`. The diagnostic gives the row count and the net `EffectiveCost` change per billing currency, so a reconciliation against the source can explain the difference. |
| `FDT-MIG-002` | The pricing and quantity columns MUST be null when `SkuPriceId` is null. The columns concerned: `ListUnitPrice` (C-012), `ContractedUnitPrice` (C-014), both pricing-currency unit prices (C-011), `PricingCategory` (C-012), `PricingQuantity` (C-011), `ConsumedQuantity` (C-009) and `CommitmentDiscountQuantity` (C-016). 1.3 made this explicit; 1.2 only said these values "MAY be null". | When the source carries a `SkuPriceId` column and the value is null, those columns are nulled. Each unit follows its quantity (`PricingUnit`, `ConsumedUnit`, `CommitmentDiscountUnit`, rules C-005/C-006). A source without a `SkuPriceId` column is left untouched: the column's absence does not mean a null SKU price. |
| `FDT-MIG-003` | `PricingCurrency` and `PricingCurrencyEffectiveCost` MUST NOT be null. 1.2 already required this; only its constraint table said otherwise, which erratum #9/#10 later corrected. | Null values in a source column that is present are backfilled from `BillingCurrency` and `EffectiveCost`. When the source omits the columns entirely, the same backfill applies without a warning, because the provider does not price in another currency. |

## Values the converter refuses to invent

Conversion stops with a `ConversionError` and publishes nothing:

- `FDT-MIG-010`: a Tax row's `EffectiveCost` must change (`FDT-MIG-001`), but its
  `PricingCurrency` differs from `BillingCurrency`. Restating `PricingCurrencyEffectiveCost` would
  need an exchange rate the source does not carry.
- `FDT-MIG-011`: `PricingCurrencyEffectiveCost` is null, and `PricingCurrency` differs from
  `BillingCurrency`. Copying `EffectiveCost`, a billing-currency amount, would label it with
  another currency.
- A `ContractApplied` value that does not fit the 1.4 object schema is also refused: an element
  carrying both cost and quantity, or a custom key without the `x_` prefix. The pre-erratum 1.3
  key casing is normalized instead, with the warning `FDT-CA-001`.

## 1.4 rules the converter cannot verify or meet

- **Meaning shifts that the row data cannot reveal.**
  - `ChargeClass` "Correction" now refers to a previously *closed* billing period, where 1.3 said
    *invoiced*.
  - Rebates tied to usage thresholds may be "Adjustment" rather than "Credit" in 1.4.
  - A Purchase covering related, postpaid charges must have `EffectiveCost` 0.

  Values are copied as they are.
- **Virtual currencies.** 1.4 CurrencyFormat requires ISO 4217 codes, while the `PricingCurrency`
  definition and the v1.4 example data still use virtual currencies such as "Token". The linter
  applies CurrencyFormat, so a virtual pricing currency fails the lint. This is a conflict inside
  the specification, not a converter choice.
- **Columns that cannot be derived.** `CommitmentProgramEligibilityDetails`, `InvoiceDetailId`,
  and an `InvoiceId` the source omits are emitted null. `InvoiceDetailId` can come from an
  `invoice_line` supplement.
- **Contract Commitment.** The 1.4 dataset adds 13 non-null mandatory columns that a 1.3 Contract
  Commitment dataset does not carry. In strict mode they come only from
  [supplements](supplements.md).
- **Dataset-level obligations.** Dataset completeness, delivery and configuration attributes
  (`CorrectionHandling`, `DeliveryHandling`, `DatasetCompleteness`) concern how a provider
  publishes data, not individual rows.
