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
  source, `ServiceProviderName` and `HostProviderName` are derived as described in
  [Participating entities of a 1.2 source](#participating-entities-of-a-12-source).
- Columns new in 1.3 or 1.4 that the source does not carry are emitted null, for example
  `CommitmentProgramEligibilityDetails` and `InvoiceDetailId`. The manifest records them as
  `UNAVAILABLE`.
- A unit-price column the source does not carry is **omitted** instead: `ListUnitPrice`,
  `ContractedUnitPrice`, `PricingCurrencyListUnitPrice` and
  `PricingCurrencyContractedUnitPrice`.
  - These conditional columns MUST NOT be null when `SkuPriceId` is set
    (CAU-ListUnitPrice-C-013, CAU-ContractedUnitPrice-C-015, and C-012 of both pricing-currency
    unit prices).
  - A source that does not carry one has not met its presence condition, and an all-null
    column would fail those rules in any validator.
  - A column counts as carried when any source row carries it.
  - The manifest lists no rule for an omitted column.

## Participating entities of a 1.2 source

FOCUS 1.2 has `ProviderName`, `PublisherName` and `InvoiceIssuerName`. FOCUS 1.3 introduced
`ServiceProviderName` "as a replacement for ProviderName", added `HostProviderName`, and
deprecated `ProviderName` and `PublisherName`. FOCUS 1.4 removes those two; there
`ServiceProviderName` is Mandatory and not nullable, and `HostProviderName` is Mandatory and
nullable. Only these official columns are read; provider-specific `x_` columns are not.

**What the specification says.**

- FOCUS 1.2 appendix *Origination of Cost Data*: when `PublisherName` differs from
  `ProviderName`, the row is either a cloud marketplace purchase (Provider = the cloud provider,
  Publisher = the seller; scenarios 3.1 to 3.3) or cloud services bought through an MSP
  (Provider = the MSP, Publisher = the cloud provider; scenario 2.1).
- FOCUS 1.4 `ServiceProviderName`: "In marketplace scenarios, the Service Provider represents the
  seller rather than the marketplace operator"; a reseller of white-labeled services is the
  Service Provider. The appendix *Participating Entity Identification* gives the Marketplace
  Seller in scenarios 3.1.1 to 3.3.2 and the MSP in scenarios 2.1 and 2.2.
- FOCUS 1.4 `HostProviderName` MUST reflect the host when the customer selected it or the
  service provider exposes it, and MUST match `ServiceProviderName` in all other cases.

**What the converter does.** A 1.2 row cannot tell the marketplace case from the MSP case, so the
caller declares who issued the file (`provider_role`, CLI `--provider-role`):

| Declaration | Row whose `PublisherName` differs from `ProviderName` | Code |
|---|---|---|
| none (default) | `ServiceProviderName` = `ProviderName`, as before; the rows are counted | `FDT-CTX-005` (warning) |
| `csp`: a cloud provider issued the file | Marketplace charge: `ServiceProviderName` = `PublisherName` (the seller) | `FDT-MIG-005` (info) |
| `msp`: an MSP or reseller issued the file | `ServiceProviderName` = `ProviderName` (the MSP) | none |

- **First-party publishers.** A cloud provider may spell itself differently in the two columns (for
  example `ProviderName` "Microsoft Azure" and `PublisherName` "Microsoft"). With `csp`, such names
  are declared as first-party publishers (`first_party_publishers`, CLI
  `--first-party-publisher`, case-insensitive), and their rows are not treated as Marketplace
  charges. The comparison with `ProviderName` is case-insensitive as well.
- **Nothing is refused.** Without a declaration, strict mode still produces the dataset; the
  warning names the publishers concerned.
- **1.3 sources.** They carry `ServiceProviderName` themselves; the declaration does not apply.
- **Manifest.** The declaration is recorded in the `ServiceProviderName` provenance note.

**`HostProviderName` is an interpretation.** A 1.2 source has no host column, so it never exposes
the host, and `HostProviderName` takes the `ServiceProviderName` value. The specification does
not settle this for a 1.2 source, and the true 1.4 value can differ in two cases:

- a Marketplace offering that runs on the cloud provider's infrastructure, or that exposes its
  host, has the cloud provider as host (appendix 3.1.x and 3.2.x), where the converter writes the
  seller (3.3.x, without visibility);
- in an MSP file, the 1.2 `PublisherName` is the cloud provider, which could be read as exposing
  the host (appendix 2.1), where the converter writes the MSP (2.2). The publisher is never taken
  as the host.

## Values migrated to a 1.4 rule

These changes are deterministic. Each one is counted and reported as a warning in the manifest.
The columns concerned are labelled `DERIVED`, the weakest lineage the rule can produce. In the
per-value `lineage_summary`, a migrated value counts as `DERIVED` and a value copied unchanged
(including those kept under `FDT-MIG-004`) as `OBSERVED`.

| Code | Rule | What changes |
|---|---|---|
| `FDT-MIG-001` | CAU-EffectiveCost-C-017: `EffectiveCost` MUST equal `BilledCost` when `ChargeCategory` is "Tax" or "Credit". In 1.3 a tax's effective cost followed the related charges' effective cost (rule C-009, removed in 1.4). | On **Tax** rows, `EffectiveCost` takes the `BilledCost` value. A non-null `PricingCurrencyEffectiveCost` follows it when `PricingCurrency` equals `BillingCurrency`. The diagnostic gives the row count and the net `EffectiveCost` change per billing currency (exact Decimal), so a reconciliation against the source can explain the difference. **Credit** rows are not rewritten. 1.2 and 1.3 tied the equivalent rule to whether a charge relates to other charges (Credit was only an example), which a row does not reveal; a credit that breaks C-017 is copied as is. |
| `FDT-MIG-002` | The pricing and quantity columns MUST be null when `SkuPriceId` is null. The columns concerned: `ListUnitPrice` (C-012), `ContractedUnitPrice` (C-014), both pricing-currency unit prices (C-011), `PricingCategory` (C-012), `PricingQuantity` (C-011), `ConsumedQuantity` (C-009) and `CommitmentDiscountQuantity` (C-016). 1.3 already states these rules, so they migrate 1.2 sources; 1.2 only said these values "MAY be null". | When the source carries a `SkuPriceId` column and the value is null, those columns are nulled on Tax, Credit and Adjustment rows and on any Correction row. Each unit follows its quantity (`PricingUnit`, `ConsumedUnit`, `CommitmentDiscountUnit`, rules C-005/C-006). A source without a `SkuPriceId` column is left untouched: the column's absence does not mean a null SKU price. |
| `FDT-MIG-004` | On a Usage or Purchase row that is not a correction, the same columns MUST NOT be null (for example `ListUnitPrice` C-005, `PricingQuantity` C-005, `ConsumedQuantity` C-006, `PricingCategory` C-004). With a null `SkuPriceId`, FOCUS 1.4 requires them both null and non-null. A row whose `ChargeCategory` is missing or not an allowed value cannot tell which rule applies. | The values are **kept** on every row the `FDT-MIG-002` nulling does not cover, because real consumption is never discarded. The rows are counted per charge category (`(null)` when missing). The source is at fault: a Usage or Purchase row should carry a `SkuPriceId`, and every row a valid `ChargeCategory`. |
| `FDT-MIG-003` | `PricingCurrency` and `PricingCurrencyEffectiveCost` MUST NOT be null. 1.2 already required this; only its constraint table said otherwise, which erratum #9/#10 later corrected. | Null values in a source column that is present are backfilled from `BillingCurrency` and `EffectiveCost`. When the source omits the columns entirely, the same backfill applies without a warning, because the provider does not price in another currency. |

## Values the converter refuses to invent

Conversion stops with a `ConversionError` and publishes nothing:

- `FDT-MIG-010`: a Tax row's `EffectiveCost` must change (`FDT-MIG-001`), but its
  `PricingCurrency` differs from `BillingCurrency`. Restating `PricingCurrencyEffectiveCost` would
  need an exchange rate the source does not carry.
- `FDT-MIG-011`: `PricingCurrencyEffectiveCost` is null, and `PricingCurrency` is set but
  differs from `BillingCurrency` or `BillingCurrency` is null. Copying `EffectiveCost`, an amount
  in the billing currency, would label it with another or an unknown currency.
- A `ContractApplied` value that does not fit the 1.4 object schema: an element carrying both
  cost and quantity, or a custom key without the `x_` prefix. The pre-erratum 1.3 key casing is
  normalized instead, with the warning `FDT-CA-001`.

Every refusal is a `ConversionError`, and the CLI exits with code 2. The `FDT-MIG-010` and
`FDT-MIG-011` messages start with the code and name the first offending row.

## 1.4 rules the converter cannot verify or meet

- **Meaning shifts that the row data cannot reveal.**
  - `ChargeClass` "Correction" now refers to a previously *closed* billing period, where 1.3 said
    *invoiced*.
  - Rebates tied to usage thresholds may be "Adjustment" rather than "Credit" in 1.4.
  - A Purchase covering related, postpaid charges must have `EffectiveCost` 0.

  Values are copied as they are.
- **Credit `EffectiveCost`.** CAU-EffectiveCost-C-017 requires `EffectiveCost` to equal
  `BilledCost` on Credit rows as well as Tax rows. 1.2 and 1.3 tied the equivalent rule to
  whether a charge relates to other charges, which a row does not reveal, so a Credit row is
  copied as is (see `FDT-MIG-001`) and may break C-017.
- **Virtual currencies.** 1.4 CurrencyFormat requires ISO 4217 codes, while the `PricingCurrency`
  definition and the v1.4 example data still use virtual currencies such as "Token". The linter
  applies CurrencyFormat, so a virtual pricing currency fails the lint. This is a conflict inside
  the specification, not a converter choice.
- **A source without a `SkuPriceId` column.** The output still carries the 1.4 `SkuPriceId`
  column, emitted null. A copied price or quantity beside it breaks the "MUST be null when
  `SkuPriceId` is null" rules (C-009 to C-016). The converter cannot tell a provider without SKU
  prices from a missing column, so this is a known non-conformance of such outputs.
- **Amounts with an exponent beyond ±1000.** They are not money, and exact bookkeeping on them
  could need billions of digits. A Tax row carrying one is copied as is, like an unparseable
  amount, instead of being migrated by `FDT-MIG-001`.
- **Columns that cannot be derived.** `CommitmentProgramEligibilityDetails`, `InvoiceDetailId`,
  and an `InvoiceId` the source omits are emitted null. `InvoiceDetailId` can come from an
  `invoice_line` supplement.
- **Contract Commitment.** The 1.4 dataset adds 13 non-null mandatory columns that a 1.3 Contract
  Commitment dataset does not carry. In strict mode they come only from
  [supplements](supplements.md).
- **Dataset-level obligations.** Dataset completeness, delivery and configuration attributes
  (`CorrectionHandling`, `DeliveryHandling`, `DatasetCompleteness`) concern how a provider
  publishes data, not individual rows.
