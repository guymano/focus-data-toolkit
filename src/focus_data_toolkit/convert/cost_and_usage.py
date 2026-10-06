"""Convert FOCUS 1.2/1.3 Cost and Usage rows to the FOCUS 1.4 column set.

FOCUS 1.4 Cost and Usage keeps 1.3's 65-column count but:

* removes the deprecated ``ProviderName`` / ``PublisherName`` (superseded by
  the 1.3 ``ServiceProviderName`` / ``HostProviderName`` split);
* adds ``CommitmentProgramEligibilityDetails`` and ``InvoiceDetailId``
  (both conditional and nullable).

A 1.2 source is first lifted to the 1.3 shape: ``ServiceProviderName`` is
derived from ``ProviderName`` (its 1.3 replacement), ``HostProviderName``
takes the ``ServiceProviderName`` value — FOCUS requires the host to match
the service provider when the source does not expose the underlying host,
and a 1.2 source never exposes it. The deprecated ``PublisherName`` ("entity
that produced the service") is dropped: it does not identify the host. The
1.3-only columns (Split Cost Allocation set, ``ContractApplied``) are null.

Some values that were valid in 1.2/1.3 are not valid in 1.4. Where the 1.4 rule fixes
the value deterministically, the row is migrated and the change is counted
(:class:`CostAndUsageMigrations`, surfaced as ``FDT-MIG-*`` diagnostics):

* a Tax charge's ``EffectiveCost`` equals its ``BilledCost`` (1.4
  CAU-EffectiveCost-C-017; 1.3 derived it from the related charges' effective cost);
* the pricing and quantity columns are null when ``SkuPriceId`` is null (1.3 made this
  explicit, 1.2 only said they "MAY be null"); a unit follows its quantity. On a Usage or
  Purchase row that is not a correction, the same columns MUST NOT be null, so FOCUS
  cannot be met there: those values are kept and the conflict is reported instead.

A value that cannot be migrated without inventing a fact raises
:class:`CostAndUsageMigrationError`, so nothing partial is published.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import MAX_EMAX, MAX_PREC, MIN_EMIN, Context, Decimal, InvalidOperation

from focus_data_toolkit.convert.contract_applied import migrate_1_3_to_1_4
from focus_data_toolkit.convert.exceptions import ConversionError
from focus_data_toolkit.convert.invoice_detail import GrainKey, invoice_detail_grain_key
from focus_data_toolkit.errors import Diagnostic, Severity
from focus_data_toolkit.model import dataset_columns
from focus_data_toolkit.provenance import ColumnRule, Lineage, LineageCounters

DATASET = "Cost and Usage"

# Columns FOCUS 1.4 requires to be null when SkuPriceId is null (CAU-ListUnitPrice-C-012,
# CAU-ContractedUnitPrice-C-014, CAU-PricingCurrencyListUnitPrice-C-011,
# CAU-PricingCurrencyContractedUnitPrice-C-011, CAU-PricingCategory-C-012,
# CAU-PricingQuantity-C-011, CAU-ConsumedQuantity-C-009,
# CAU-CommitmentDiscountQuantity-C-016). FOCUS 1.2 allowed values there.
NULL_WHEN_SKU_PRICE_ID_NULL: tuple[str, ...] = (
    "ListUnitPrice",
    "ContractedUnitPrice",
    "PricingCurrencyListUnitPrice",
    "PricingCurrencyContractedUnitPrice",
    "PricingCategory",
    "PricingQuantity",
    "ConsumedQuantity",
    "CommitmentDiscountQuantity",
)
# A unit MUST be null exactly when its quantity is (CAU-PricingUnit-C-005/006,
# CAU-ConsumedUnit-C-005/006, CAU-CommitmentDiscountUnit-C-005/006), so a quantity
# nulled above takes its unit with it.
UNIT_OF_QUANTITY: dict[str, str] = {
    "PricingQuantity": "PricingUnit",
    "ConsumedQuantity": "ConsumedUnit",
    "CommitmentDiscountQuantity": "CommitmentDiscountUnit",
}
_SKU_PRICE_CASCADE: tuple[str, ...] = NULL_WHEN_SKU_PRICE_ID_NULL + tuple(
    UNIT_OF_QUANTITY.values()
)
# Exact arithmetic for migration bookkeeping: source amounts may carry more digits than the
# default 28-digit context, and a reported delta must never be rounded. Amounts are bounded
# to exponents within +/-_MAX_EXPONENT (see _decimal), so an exact result stays small.
_EXACT = Context(prec=MAX_PREC, Emax=MAX_EMAX, Emin=MIN_EMIN)
_MAX_EXPONENT = 1000


def _cascade_applies(converted: Mapping[str, str]) -> bool:
    """Whether nulling the ``SkuPriceId``-dependent columns is consistent with FOCUS 1.4 here.

    Only on Tax, Credit and Adjustment charges and on corrections. For Usage and Purchase
    charges that are not corrections, ``ListUnitPrice`` (C-005), ``ContractedUnitPrice``
    (C-006), ``PricingCategory`` (C-004), ``PricingQuantity`` (C-005), ``ConsumedQuantity``
    (C-006), ``CommitmentDiscountQuantity`` (C-005) and the pricing-currency unit prices
    (C-005) MUST NOT be null, which contradicts nulling them; a missing or unknown
    ``ChargeCategory`` cannot tell which rule applies. Both keep their values.
    """
    if (converted.get("ChargeClass") or "").strip() == "Correction":
        return True
    return (converted.get("ChargeCategory") or "").strip() in ("Tax", "Credit", "Adjustment")


class CostAndUsageMigrationError(ConversionError):
    """A 1.2/1.3 value cannot be brought to a FOCUS 1.4 rule without inventing a fact."""


@dataclass
class CostAndUsageMigrations:
    """Counts of the row values migrated to the FOCUS 1.4 rules during one conversion.

    One accumulator is shared by the eager and streaming pipelines (like ``legacy_keys``),
    so both render identical ``FDT-MIG-*`` diagnostics through :func:`migration_diagnostics`.
    """

    # Tax rows whose EffectiveCost was set to BilledCost, and the net EffectiveCost change,
    # both keyed by BillingCurrency (amounts in different currencies never add up).
    tax_effective_cost_rows: Counter[str] = field(default_factory=Counter)
    tax_effective_cost_delta: dict[str, Decimal] = field(default_factory=dict)
    # Values nulled because SkuPriceId is null, per column.
    nulled_without_sku_price: Counter[str] = field(default_factory=Counter)
    # Rows without SkuPriceId whose pricing values were kept, per ChargeCategory ("(null)"
    # when missing): Usage/Purchase rows that are not corrections, where FOCUS 1.4 requires
    # them both null and non-null, and rows whose category does not tell which rule applies.
    kept_without_sku_price: Counter[str] = field(default_factory=Counter)
    # Null values in a pricing-currency column the source does carry, backfilled from the
    # billing-currency value. FOCUS 1.2 already required those values to be non-null.
    backfilled_source_nulls: Counter[str] = field(default_factory=Counter)

    def __bool__(self) -> bool:
        return bool(
            self.tax_effective_cost_rows
            or self.nulled_without_sku_price
            or self.kept_without_sku_price
            or self.backfilled_source_nulls
        )


def migration_diagnostics(migrations: CostAndUsageMigrations) -> list[Diagnostic]:
    """Render the migrations as deterministic ``FDT-MIG-*`` warnings (empty when none)."""
    out: list[Diagnostic] = []
    if migrations.tax_effective_cost_rows:
        rows = sum(migrations.tax_effective_cost_rows.values())
        out.append(
            Diagnostic(
                code="FDT-MIG-001",
                severity=Severity.WARNING,
                message=(
                    f"{rows} Tax row(s): EffectiveCost set to BilledCost "
                    "(FOCUS 1.4 CAU-EffectiveCost-C-017); total EffectiveCost changes by "
                    "the delta shown per billing currency"
                ),
                datasets=(DATASET,),
                column="EffectiveCost",
                rule="CAU-EffectiveCost-C-017",
                context={
                    "rows_by_currency": "; ".join(
                        f"{c}:{n}" for c, n in sorted(migrations.tax_effective_cost_rows.items())
                    ),
                    "effective_cost_delta_by_currency": "; ".join(
                        f"{c}:{format(d, 'f')}"
                        for c, d in sorted(migrations.tax_effective_cost_delta.items())
                    ),
                },
            )
        )
    if migrations.nulled_without_sku_price:
        values = sum(migrations.nulled_without_sku_price.values())
        out.append(
            Diagnostic(
                code="FDT-MIG-002",
                severity=Severity.WARNING,
                message=(
                    f"{values} value(s) nulled where SkuPriceId is null on Tax, Credit, "
                    "Adjustment or Correction rows (FOCUS 1.3+ requires these columns to be "
                    "null there; a unit follows its quantity)"
                ),
                datasets=(DATASET,),
                column="SkuPriceId",
                context={
                    "values_by_column": "; ".join(
                        f"{c}:{n}" for c, n in sorted(migrations.nulled_without_sku_price.items())
                    )
                },
            )
        )
    if migrations.kept_without_sku_price:
        rows = sum(migrations.kept_without_sku_price.values())
        out.append(
            Diagnostic(
                code="FDT-MIG-004",
                severity=Severity.WARNING,
                message=(
                    f"{rows} row(s) without SkuPriceId keep their pricing and quantity values. "
                    "On Usage/Purchase rows that are not corrections, FOCUS 1.4 requires those "
                    "columns to be both null (no SkuPriceId) and non-null, so the source omits a "
                    "SkuPriceId it should carry; a row with a missing or unknown ChargeCategory "
                    "is never nulled"
                ),
                datasets=(DATASET,),
                column="SkuPriceId",
                context={
                    "rows_by_charge_category": "; ".join(
                        f"{c}:{n}" for c, n in sorted(migrations.kept_without_sku_price.items())
                    )
                },
            )
        )
    if migrations.backfilled_source_nulls:
        values = sum(migrations.backfilled_source_nulls.values())
        out.append(
            Diagnostic(
                code="FDT-MIG-003",
                severity=Severity.WARNING,
                message=(
                    f"{values} null value(s) in source pricing-currency columns backfilled from "
                    "the billing-currency values (FOCUS requires them to be non-null)"
                ),
                datasets=(DATASET,),
                column="PricingCurrency",
                context={
                    "values_by_column": "; ".join(
                        f"{c}:{n}" for c, n in sorted(migrations.backfilled_source_nulls.items())
                    )
                },
            )
        )
    return out


def _decimal(text: str) -> Decimal | None:
    """Parse an amount for exact bookkeeping; ``None`` when unusable.

    An amount whose exponent lies beyond ``_MAX_EXPONENT`` is not money, and exact arithmetic
    on it could need billions of digits, so it is treated like an unparseable one.
    """
    try:
        value = Decimal(text.strip())
    except (InvalidOperation, ValueError):
        return None
    exponent = value.as_tuple().exponent  # a str for NaN and Infinity
    if not isinstance(exponent, int) or max(abs(exponent), abs(value.adjusted())) > _MAX_EXPONENT:
        return None
    return value


def contract_applied_legacy_diagnostic(legacy_keys: set[str]) -> Diagnostic | None:
    """Build the ``FDT-CA-001`` diagnostic when legacy 1.3 casing was normalized.

    Accepting the pre-erratum ``ContractID``/``ContractCommitmentID`` casing is a
    compatibility normalization, never silent equivalence: the encounter is surfaced
    so callers do not mistake a merely tolerated file for a conformant one. Returns
    ``None`` when no legacy casing was seen. Shared by the eager and streaming
    pipelines so both manifests stay identical.
    """
    if not legacy_keys:
        return None
    return Diagnostic(
        code="FDT-CA-001",
        severity=Severity.WARNING,
        message=(
            "legacy FOCUS 1.3 ContractApplied identifier casing normalized "
            f"({', '.join(sorted(legacy_keys))} -> canonical Id casing per "
            "FOCUS 1.3 erratum #3)"
        ),
        datasets=(DATASET,),
        column="ContractApplied",
    )

# 1.2 -> 1.3/1.4 participant-entity derivations. Both columns derive from
# ProviderName: FOCUS 1.3 replaced ProviderName with ServiceProviderName, and the
# HostProviderName rules require the value to match ServiceProviderName when the
# source does not expose the underlying host (a 1.2 source never does). The
# deprecated PublisherName is NOT a host equivalent and is dropped with the other
# removed 1.2 columns.
_DERIVED_FROM_1_2 = {
    "ServiceProviderName": "ProviderName",
    "HostProviderName": "ProviderName",
}


# Conditional unit-price columns whose rules depend on their presence: "MUST NOT be null when
# SkuPriceId is not null" (CAU-ListUnitPrice-C-013, CAU-ContractedUnitPrice-C-015, and C-012 of
# both pricing-currency unit prices) holds only where the column exists. A source without the
# column has not met its presence condition, so the 1.4 output omits it too; written all null,
# it would fail any validator applying those rules.
OMITTED_WHEN_ABSENT_FROM_SOURCE: tuple[str, ...] = (
    "ListUnitPrice",
    "ContractedUnitPrice",
    "PricingCurrencyListUnitPrice",
    "PricingCurrencyContractedUnitPrice",
)


def source_header(rows: Iterable[Mapping[str, str]]) -> tuple[str, ...]:
    """Every column any of ``rows`` carries, in first-seen order: the header of in-memory rows.

    Rows read from a file share one header; dicts built by a caller may not, and a column only
    some rows carry is still present in the source (a row without the key has a null value).
    """
    header: dict[str, None] = {}
    for row in rows:
        header.update(dict.fromkeys(row))
    return tuple(header)


def emitted_cost_and_usage_columns(source_columns: Iterable[str]) -> tuple[str, ...]:
    """The 1.4 Cost and Usage columns written for a source carrying ``source_columns``."""
    present = set(source_columns)
    return tuple(
        c for c in dataset_columns(DATASET)
        if c in present or c not in OMITTED_WHEN_ABSENT_FROM_SOURCE
    )


def cost_and_usage_provenance(
    source_columns: Iterable[str], source_version: str, *, invoice_detail_linked: bool
) -> dict[str, ColumnRule]:
    """Return the per-column lineage of a converted Cost and Usage dataset.

    ``invoice_detail_linked`` is True when an (synthetic) Invoice Detail dataset is being
    produced, so ``InvoiceDetailId`` carries the back-link (assumed); otherwise it is null.
    Columns the output omits (see :func:`emitted_cost_and_usage_columns`) have no rule.
    """
    present = set(source_columns)
    sku_price_cascade = "SkuPriceId" in present
    rules: dict[str, ColumnRule] = {}
    for col in emitted_cost_and_usage_columns(present):
        if col == "ContractApplied":
            rules[col] = (
                ColumnRule(Lineage.DERIVED, "ContractApplied migrated 1.3->1.4")
                if source_version == "1.3" and "ContractApplied" in present
                else ColumnRule(Lineage.UNAVAILABLE, note="emitted null")
            )
        elif col == "PricingCurrency":
            # Non-nullable in 1.4; source value where present, nulls backfilled from
            # billing-currency values -> derived at the column level (not plain observed).
            rules[col] = ColumnRule(
                Lineage.DERIVED, "source value; nulls backfilled from billing-currency values"
            )
        elif col == "PricingCurrencyEffectiveCost":
            rules[col] = ColumnRule(
                Lineage.DERIVED,
                "source value; nulls backfilled from billing-currency values",
                note="follows EffectiveCost on Tax rows migrated by CAU-EffectiveCost-C-017",
            )
        elif col == "EffectiveCost" and col in present:
            # Headline = the weakest lineage the rule can produce (see provenance.py).
            rules[col] = ColumnRule(
                Lineage.DERIVED,
                "CostAndUsage.EffectiveCost",
                note="Tax rows set to BilledCost (FOCUS 1.4 CAU-EffectiveCost-C-017)",
            )
        elif sku_price_cascade and col in _SKU_PRICE_CASCADE and col in present:
            rules[col] = ColumnRule(
                Lineage.DERIVED,
                f"CostAndUsage.{col}",
                note=(
                    "nulled where SkuPriceId is null on Tax, Credit, Adjustment and correction "
                    "rows; kept on any other row (FDT-MIG-004)"
                ),
            )
        elif col in present:
            rules[col] = ColumnRule(Lineage.OBSERVED, f"CostAndUsage.{col}")
        elif source_version == "1.2" and col == "ServiceProviderName":
            rules[col] = ColumnRule(
                Lineage.DERIVED,
                "ProviderName",
                note="FOCUS 1.3 replaced ProviderName with ServiceProviderName",
            )
        elif source_version == "1.2" and col == "HostProviderName":
            rules[col] = ColumnRule(
                Lineage.DERIVED,
                "ServiceProviderName (from ProviderName)",
                note=(
                    "host not exposed by a 1.2 source; FOCUS requires "
                    "HostProviderName to match ServiceProviderName in that case"
                ),
            )
        elif col == "InvoiceDetailId":
            # A locally generated hash presented as an issuer-assigned id -> assumed
            # when linked (so synthetic Cost and Usage is labelled synthetic); else null.
            rules[col] = (
                ColumnRule(
                    Lineage.ASSUMED, note="locally generated back-link to synthetic Invoice Detail"
                )
                if invoice_detail_linked
                else ColumnRule(Lineage.UNAVAILABLE, note="emitted null (Invoice Detail not produced)")
            )
        else:
            rules[col] = ColumnRule(Lineage.UNAVAILABLE, note="emitted null")
    return rules


def _convert_contract_applied(
    raw: str | None, source_version: str, legacy_keys: set[str] | None = None
) -> str:
    """Migrate a source ``ContractApplied`` JSON to the FOCUS 1.4 schema.

    Enforces 1.4 metric exclusivity and normalizes any legacy pre-erratum 1.3
    identifier casing (``ContractID``/``ContractCommitmentID`` -> the canonical
    ``ContractId``/``ContractCommitmentId``), recording the encountered legacy keys
    in ``legacy_keys`` so the pipeline can surface the normalization. A 1.2 source
    has no ``ContractApplied`` column, so the value is empty there. Raises
    ``ContractAppliedError`` (a ``ValueError``) on a structurally invalid source value.
    """
    text = (raw or "").strip()
    if not text or source_version != "1.3":
        return text
    return migrate_1_3_to_1_4(text, legacy_sink=legacy_keys)


def _row_key(converted: Mapping[str, str]) -> str:
    """A short business key that locates a row in an error message."""
    return ", ".join(
        f"{col}={converted.get(col, '')!r}"
        for col in ("BillingAccountId", "ChargePeriodStart", "InvoiceId", "ResourceId")
        if converted.get(col)
    )


def _migrate_tax_effective_cost(
    converted: dict[str, str], migrations: CostAndUsageMigrations | None
) -> bool:
    """Set a Tax row's ``EffectiveCost`` to its ``BilledCost`` (CAU-EffectiveCost-C-017).

    Returns whether the value changed. Unparseable amounts are left untouched for the
    linter to report; the source text of ``BilledCost`` is copied verbatim.
    """
    if (converted.get("ChargeCategory") or "").strip() != "Tax" or "EffectiveCost" not in converted:
        return False
    billed = _decimal(converted.get("BilledCost") or "")
    effective_text = (converted.get("EffectiveCost") or "").strip()
    effective = _decimal(effective_text) if effective_text else Decimal(0)
    if billed is None or effective is None or (effective_text and effective == billed):
        return False
    converted["EffectiveCost"] = converted["BilledCost"]
    if migrations is not None:
        currency = (converted.get("BillingCurrency") or "").strip()
        migrations.tax_effective_cost_rows[currency] += 1
        migrations.tax_effective_cost_delta[currency] = _EXACT.add(
            migrations.tax_effective_cost_delta.get(currency, Decimal(0)),
            _EXACT.subtract(billed, effective),
        )
    return True


def _null_without_sku_price(
    row: Mapping[str, str], converted: dict[str, str], migrations: CostAndUsageMigrations | None
) -> frozenset[str]:
    """Null the columns FOCUS 1.4 requires to be null when ``SkuPriceId`` is null.

    Applies only when the source carries a ``SkuPriceId`` column: a provider without SKU
    prices does not supply it, and its absence is not a null SKU price. Only Tax, Credit,
    Adjustment and correction rows are nulled (see :func:`_cascade_applies`). On any other row
    the values are kept (real consumption is never discarded) and the row is counted for
    ``FDT-MIG-004``. Returns the columns whose value was removed.
    """
    if "SkuPriceId" not in row or (row.get("SkuPriceId") or "").strip():
        return frozenset()
    if not _cascade_applies(converted):
        if migrations is not None and any(
            (converted.get(col) or "").strip() for col in NULL_WHEN_SKU_PRICE_ID_NULL
        ):
            charge = (converted.get("ChargeCategory") or "").strip() or "(null)"
            migrations.kept_without_sku_price[charge] += 1
        return frozenset()
    nulled: set[str] = set()
    for col in NULL_WHEN_SKU_PRICE_ID_NULL:
        if (converted.get(col) or "").strip():
            converted[col] = ""
            nulled.add(col)
            unit = UNIT_OF_QUANTITY.get(col)
            if unit is not None and (converted.get(unit) or "").strip():
                converted[unit] = ""
                nulled.add(unit)
    if migrations is not None:
        for col in nulled:
            migrations.nulled_without_sku_price[col] += 1
    return frozenset(nulled)


def convert_cost_and_usage_row(
    row: Mapping[str, str],
    source_version: str,
    *,
    detail_id: str = "",
    target: tuple[str, ...] | None = None,
    counters: LineageCounters | None = None,
    legacy_keys: set[str] | None = None,
    migrations: CostAndUsageMigrations | None = None,
) -> dict[str, str]:
    """Convert one source row to the FOCUS 1.4 Cost and Usage shape (pure function).

    ``detail_id`` is the already-resolved ``InvoiceDetailId`` back-link (empty in strict mode
    or for rows with no invoice). Shared by the eager and streaming pipelines so both produce
    identical output. ``counters`` (optional) records the per-value lineage of columns whose
    rule varies by row (the migrated columns and the pricing-currency backfill pair);
    ``legacy_keys`` (optional) collects legacy pre-erratum ``ContractApplied`` identifier
    casings that were normalized; ``migrations`` (optional) counts the values migrated to
    the FOCUS 1.4 rules. Raises :class:`CostAndUsageMigrationError` when a 1.4 rule cannot
    be met without inventing a value.
    """
    columns = target if target is not None else dataset_columns(DATASET)
    converted: dict[str, str] = {}
    for col in columns:
        if col == "ContractApplied":
            converted[col] = _convert_contract_applied(row.get(col), source_version, legacy_keys)
        elif col in row:
            converted[col] = row[col]
        elif source_version == "1.2" and col in _DERIVED_FROM_1_2:
            converted[col] = row.get(_DERIVED_FROM_1_2[col], "")
        elif col == "InvoiceDetailId":
            converted[col] = detail_id
        else:
            # New-in-1.4 or 1.3-only columns absent from the source: null.
            converted[col] = ""

    effective_changed = _migrate_tax_effective_cost(converted, migrations)
    pricing_effective_changed = False
    if effective_changed and (converted.get("PricingCurrencyEffectiveCost") or "").strip():
        # A non-null source value must follow the new EffectiveCost. That is factual only when
        # the pricing currency is the billing currency; any other currency needs an exchange
        # rate the source does not carry.
        billing = (converted.get("BillingCurrency") or "").strip()
        pricing = (converted.get("PricingCurrency") or "").strip() or billing
        if pricing != billing:
            raise CostAndUsageMigrationError(
                "FDT-MIG-010: FOCUS 1.4 requires a Tax row's EffectiveCost to equal its "
                f"BilledCost, but its PricingCurrency {pricing!r} differs from BillingCurrency "
                f"{billing!r}, so PricingCurrencyEffectiveCost cannot be restated without an "
                f"exchange rate the source does not carry ({_row_key(converted)})"
            )
        converted["PricingCurrencyEffectiveCost"] = converted["EffectiveCost"]
        pricing_effective_changed = True
    nulled = _null_without_sku_price(row, converted, migrations)

    if counters is not None:
        if "EffectiveCost" in row:
            counters.record(
                "EffectiveCost", Lineage.DERIVED if effective_changed else Lineage.OBSERVED
            )
        if "SkuPriceId" in row:
            for col in _SKU_PRICE_CASCADE:
                if col in row and col in converted:
                    counters.record(col, Lineage.DERIVED if col in nulled else Lineage.OBSERVED)

    # FOCUS 1.4 makes the pricing-currency pair non-nullable. When a 1.x source leaves it
    # null (e.g. tax or credit rows), pricing happened in the billing currency, so backfill.
    # PricingCurrency is resolved first, so the effective-cost backfill sees its final value.
    for col, fallback in (
        ("PricingCurrency", "BillingCurrency"),
        ("PricingCurrencyEffectiveCost", "EffectiveCost"),
    ):
        if not converted.get(col):
            if col == "PricingCurrencyEffectiveCost":
                billing = (converted.get("BillingCurrency") or "").strip()
                pricing = (converted.get("PricingCurrency") or "").strip()
                if pricing and pricing != billing:
                    # EffectiveCost is denominated in BillingCurrency (unknown when it is
                    # null): copying it would label that amount with another currency.
                    raise CostAndUsageMigrationError(
                        "FDT-MIG-011: PricingCurrencyEffectiveCost is null but cannot be "
                        f"backfilled from EffectiveCost: PricingCurrency {pricing!r} differs from "
                        f"BillingCurrency {billing!r} and the source carries no exchange rate "
                        f"({_row_key(converted)})"
                    )
            converted[col] = converted.get(fallback, "")
            if migrations is not None and col in row:
                migrations.backfilled_source_nulls[col] += 1
            if counters is not None:
                counters.record(col, Lineage.DERIVED)
        elif counters is not None:
            changed = col == "PricingCurrencyEffectiveCost" and pricing_effective_changed
            counters.record(col, Lineage.DERIVED if changed else Lineage.OBSERVED)
    return converted


def convert_cost_and_usage(
    rows: list[dict[str, str]],
    source_version: str,
    *,
    invoice_detail_ids: dict[GrainKey, str] | None = None,
    counters: LineageCounters | None = None,
    legacy_keys: set[str] | None = None,
    migrations: CostAndUsageMigrations | None = None,
    source_columns: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    """Return ``rows`` reshaped to the FOCUS 1.4 Cost and Usage column set.

    ``invoice_detail_ids`` maps each Invoice Detail business-grain key to the
    ``InvoiceDetailId`` assigned by the Invoice Detail builder, so converted rows link back
    to their invoice line item on exactly the same key. ``source_columns`` (the source
    header; by default every column any row carries, see :func:`source_header`) decides which
    conditional columns are omitted (:func:`emitted_cost_and_usage_columns`).
    """
    if source_columns is None:
        source_columns = source_header(rows)
    target = emitted_cost_and_usage_columns(source_columns)
    ids = invoice_detail_ids or {}
    return [
        convert_cost_and_usage_row(
            row,
            source_version,
            detail_id=ids.get(invoice_detail_grain_key(row), ""),
            target=target,
            counters=counters,
            legacy_keys=legacy_keys,
            migrations=migrations,
        )
        for row in rows
    ]
