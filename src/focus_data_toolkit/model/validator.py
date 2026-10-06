"""Reference FOCUS 1.4 **structural linter** (model-driven).

This is a *linter*, not a full FOCUS 1.4 conformance validator. It checks that data
presented as a FOCUS 1.4 dataset is well-formed against the committed 1.4 data model
(``focus_1_4_model.json``) at two levels — and it can assert **only** those two:

* ``STRUCTURAL_VALID`` — required (Mandatory) columns present; unknown non-``x_``
  columns flagged; nullability; and value **format** (NumericFormat incl. scientific
  notation, Date/Time UTC ``…Z``, Currency ISO 4217, Allowed-Values enums, Unit, and
  JSON/Key-Value well-formedness with the ``x_`` custom-key rule).
* ``SEMANTIC_VALID`` — single-row cross-field rules (Tax nulls, consumption gating,
  ``LastUpdated >= Created``, ServiceSubcategory↔ServiceCategory, upfront-percentage vs
  payment model, condition-aware required columns, ContractApplied deep structure, and
  these FOCUS 1.4 Cost and Usage rules: Tax/Credit ``EffectiveCost = BilledCost`` (C-017),
  cost = unit price × ``PricingQuantity`` within the official validator's relative
  tolerance (C-011), unit/quantity pairing (C-005/C-006), list and contracted unit prices
  required with a ``SkuPriceId`` (C-013/C-015, unless the source lacks the column) and,
  under declared unit pricing, the ``SkuPriceId`` null cascade (C-009 to C-016). Other
  static rules of the v1.4 model are not checked), and
  the official FOCUS JSON object schemas (vendored verbatim in ``model/json_schemas/``)
  for ``ContractApplied``, ``AllocatedMethodDetails``,
  ``CommitmentProgramEligibilityDetails`` and ``ContractCommitmentApplicability``.

It does **not** assert ``CROSS_DATASET_VALID`` (referential integrity across the four
datasets) or ``OFFICIALLY_VALIDATED`` (the FinOps ``focus_validator``, which does not yet
support 1.4). A clean ``LintReport`` therefore means *structurally and semantically
well-formed*, **not** fully FOCUS-conformant.

``validate_focus_1_4`` is retained as a deprecated alias of
``lint_focus_1_4_structure``.
"""

from __future__ import annotations

import json
import re
import warnings
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import (
    MAX_EMAX,
    MAX_PREC,
    MIN_EMIN,
    Context,
    Decimal,
    DecimalException,
    InvalidOperation,
)
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # import cycle: capabilities re-uses the COND_* constants below
    from focus_data_toolkit.model.capabilities import CapabilityProfile

from focus_data_toolkit.model.focus_json_keys import (
    XPREFIX_ENFORCED_ELEMENTS_COLUMNS,
    XPREFIX_ENFORCED_KEYVALUE_COLUMNS,
)
from focus_data_toolkit.model.json_schema_check import (
    OFFICIAL_SCHEMA_COLUMNS,
    check_against_official_schema,
)

_HERE = Path(__file__).resolve().parent
_MODEL_PATH = _HERE / "focus_1_4_model.json"
_ISO_4217_PATH = _HERE / "iso_4217_currencies.json"

# Validation levels this linter can assert. CROSS_DATASET_VALID / OFFICIALLY_VALIDATED
# are intentionally NOT checked here (documented in the module docstring).
LEVEL_STRUCTURAL = "STRUCTURAL_VALID"
LEVEL_SEMANTIC = "SEMANTIC_VALID"
LEVEL_CROSS_DATASET = "CROSS_DATASET_VALID"
LEVEL_OFFICIAL = "OFFICIALLY_VALIDATED"
_CHECKED_LEVELS: tuple[str, ...] = (LEVEL_STRUCTURAL, LEVEL_SEMANTIC)

# Applicability conditions (FOCUS 1.4 Applicability Criteria) that gate the
# "conditionally required" columns. Callers pass the subset they declare.
COND_MULTIPLE_PRICING_CATEGORIES = "SupportsMultiplePricingCategories"
COND_UNIT_PRICING = "SupportsUnitPricing"

# Columns FOCUS 1.4 requires to be null when SkuPriceId is null (CAU-ListUnitPrice-C-012,
# CAU-ContractedUnitPrice-C-014, CAU-PricingCurrencyListUnitPrice-C-011,
# CAU-PricingCurrencyContractedUnitPrice-C-011, CAU-PricingCategory-C-012,
# CAU-PricingQuantity-C-011, CAU-ConsumedQuantity-C-009, CAU-CommitmentDiscountQuantity-C-016).
_NULL_WITHOUT_SKU_PRICE_ID: tuple[str, ...] = (
    "ListUnitPrice",
    "ContractedUnitPrice",
    "PricingCurrencyListUnitPrice",
    "PricingCurrencyContractedUnitPrice",
    "PricingCategory",
    "PricingQuantity",
    "ConsumedQuantity",
    "CommitmentDiscountQuantity",
)
# (quantity, unit) pairs: a unit is null exactly when its quantity is (CAU-*Unit-C-005/006).
_UNIT_OF_QUANTITY: tuple[tuple[str, str], ...] = (
    ("PricingQuantity", "PricingUnit"),
    ("ConsumedQuantity", "ConsumedUnit"),
    ("CommitmentDiscountQuantity", "CommitmentDiscountUnit"),
)
# Relative tolerance of the cost = unit price x quantity identity, the official
# focus-validator's (ColumnByColumnEqualsColumnValue: |a x b - r| <= 1e-9 x max(|r|, 1)).
# It exists to absorb representation rounding; being relative, it also accepts an absolute
# difference of up to 1e-9 x |cost| (about 1.2 on a cost of 1.2e9), exactly as that validator.
_PRODUCT_TOLERANCE = Decimal("1e-9")
# The identity is computed exactly and independently of the caller's Decimal context: a
# narrower context would round the product (accepting real differences). Exact arithmetic is
# only bounded when the operands are: an exponent beyond +/-_MAX_EXPONENT is not money and
# could need billions of digits, so it is reported as not computable, never computed.
_EXACT = Context(prec=MAX_PREC, Emax=MAX_EMAX, Emin=MIN_EMIN)
_MAX_EXPONENT = 1000


def _exactly_computable(*values: Decimal) -> bool:
    """Whether exact arithmetic on ``values`` stays small (every exponent within the bound)."""
    for value in values:
        exponent = value.as_tuple().exponent  # a str for NaN and Infinity
        if not isinstance(exponent, int) or max(abs(exponent), abs(value.adjusted())) > _MAX_EXPONENT:
            return False
    return True

_DATASET_ALIASES = {
    "cost and usage": "Cost and Usage", "costandusage": "Cost and Usage", "cau": "Cost and Usage",
    "billing period": "Billing Period", "billingperiod": "Billing Period", "bpd": "Billing Period",
    "contract commitment": "Contract Commitment", "contractcommitment": "Contract Commitment",
    "cct": "Contract Commitment",
    "invoice detail": "Invoice Detail", "invoicedetail": "Invoice Detail", "ind": "Invoice Detail",
}

# NumericFormat (FOCUS attribute): integer, decimal, or scientific E-notation "mEn".
# The exponent sign is expressed ONLY when negative (no leading '+' on mantissa or
# exponent). So 35.2E-7 is valid; 35.2E+7 and +333 are not.
_NUMERIC_RE = re.compile(r"-?\d+(\.\d+)?(E-?\d+)?")
# DateTimeFormat: literal YYYY-MM-DDTHH:mm:ss[.fff]Z (UTC 'Z' only, ISO 8601).
_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z")
# Formats the v1.4 specification recommends (SHOULD) for an "Expected Format" column.
# ContractCommitmentDurationType: "[Numeric Value] [Unit]", a positive whole number and one
# of the listed units, singular or plural ("1 Year", "3 Years", "36 Months"); rules
# CCT-ContractCommitmentDurationType-C-004-O/C-005-O. Being SHOULD, they never fail the lint
# of a 1.4 file; supplied facts must follow them (check_column_value).
_RECOMMENDED_FORMATS = {
    "ContractCommitmentDurationType": re.compile(
        r"[1-9]\d* (?:Minute|Hour|Day|Week|Month|Quarter|Year)s?"
    ),
}


@dataclass(frozen=True)
class Violation:
    dataset: str
    rule: str
    message: str
    column: str | None = None
    row_index: int | None = None
    level: str = LEVEL_STRUCTURAL


@dataclass(frozen=True)
class LintReport:
    dataset: str
    row_count: int
    violations: tuple[Violation, ...]
    levels_checked: tuple[str, ...] = _CHECKED_LEVELS

    @property
    def ok(self) -> bool:
        """No structural/semantic lint violations. NOT a full FOCUS conformance claim."""
        return not self.violations

    def passed(self, level: str) -> bool:
        """True if ``level`` was checked and has no violations."""
        if level not in self.levels_checked:
            return False
        return not any(v.level == level for v in self.violations)

    @property
    def levels_passed(self) -> tuple[str, ...]:
        return tuple(level for level in self.levels_checked if self.passed(level))

    def messages(self) -> list[str]:
        return [
            f"[{v.level}:{v.rule}] {v.column or '-'}"
            + (f" row {v.row_index}" if v.row_index is not None else "")
            + f": {v.message}"
            for v in self.violations
        ]


# Backwards-compatible alias (the class was previously ``ValidationReport``).
ValidationReport = LintReport


@lru_cache(maxsize=1)
def load_model() -> dict:
    return json.loads(_MODEL_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _iso_4217() -> frozenset[str]:
    return frozenset(json.loads(_ISO_4217_PATH.read_text(encoding="utf-8"))["codes"])


def resolve_dataset(name: str) -> str:
    key = name.strip().lower()
    if key not in _DATASET_ALIASES:
        raise ValueError(f"unknown FOCUS 1.4 dataset {name!r}")
    return _DATASET_ALIASES[key]


class _DuplicateKey(Exception):
    pass


class _NonFiniteConstant(Exception):
    pass


def _no_dup_pairs(pairs: list[tuple[str, object]]) -> dict:
    seen: dict = {}
    for key, value in pairs:
        if key in seen:
            raise _DuplicateKey(key)
        seen[key] = value
    return seen


def _reject_constant(const: str) -> float:
    # NaN / Infinity / -Infinity are Python extensions, not valid JSON.
    raise _NonFiniteConstant(const)


def _load_json_object(value: str) -> tuple[dict | None, str | None]:
    try:
        obj = json.loads(value, object_pairs_hook=_no_dup_pairs, parse_constant=_reject_constant)
    except _DuplicateKey:
        return None, "duplicate_json_key"
    except _NonFiniteConstant:
        return None, "bad_json"
    except json.JSONDecodeError:
        return None, "bad_json"
    if not isinstance(obj, dict):
        return None, "json_not_object"
    return obj, None


def _decimal_or_none(value: str) -> Decimal | None:
    if not _NUMERIC_RE.fullmatch(value):
        return None
    try:
        d = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _is_utc_datetime(value: str) -> bool:
    if not _DATETIME_RE.fullmatch(value):
        return False
    try:
        _parse_dt(value)
    except ValueError:
        return False
    return True


def _keys_are_focus_or_prefixed(keys: Iterable[str], focus_keys: frozenset[str]) -> bool:
    return all(k in focus_keys or k.startswith("x_") for k in keys)


def _validate_contract_applied(value: str) -> str | None:
    # Lazy import avoids an import cycle (convert -> model.validator -> convert).
    from focus_data_toolkit.convert.contract_applied import ContractAppliedError, parse

    try:
        parse(value, version="1.4")
    except ContractAppliedError:
        return "invalid_contract_applied"
    return None


def _validate_json_column(column: str, value: str, value_format: str) -> str | None:
    obj, err = _load_json_object(value)
    if err:
        return err
    assert obj is not None  # _load_json_object returns a dict whenever err is None
    if value_format == "Key-Value":
        if not all(v is None or isinstance(v, str | int | float | bool) for v in obj.values()):
            return "key_value_value_not_scalar"
        focus_keys = XPREFIX_ENFORCED_KEYVALUE_COLUMNS.get(column)
        if focus_keys is not None and not _keys_are_focus_or_prefixed(obj, focus_keys):
            return "custom_key_not_prefixed"
        return None
    # JSON Object columns.
    if column == "ContractApplied":
        err = _validate_contract_applied(value)
        if err:
            return err
    entry = XPREFIX_ENFORCED_ELEMENTS_COLUMNS.get(column)
    if entry is not None:
        array_key, focus_keys = entry
        # Top-level custom keys (alongside the array) must be x_-prefixed too.
        if not all(k == array_key or k.startswith("x_") for k in obj):
            return "custom_key_not_prefixed"
        elements = obj.get(array_key)
        if not isinstance(elements, list):
            return "missing_elements_array"
        for element in elements:
            if not isinstance(element, dict):
                return "element_not_object"
            if not _keys_are_focus_or_prefixed(element, focus_keys):
                return "custom_key_not_prefixed"
    # Normative depth: the official FOCUS JSON Schemas (vendored verbatim, see
    # model/json_schemas/) — conditional scope rules, metric exclusivity, ranges.
    if column in OFFICIAL_SCHEMA_COLUMNS and check_against_official_schema(column, obj):
        return "official_schema_violation"
    return None


def _format_violation(spec: dict, column: str, value: str) -> str | None:
    """Return a rule name if non-empty ``value`` violates the column's format."""
    value_format = spec.get("value_format") or ""
    data_type = spec.get("data_type") or ""

    if value_format.startswith("Decimal") or data_type == "Decimal":
        d = _decimal_or_none(value)
        if d is None:
            return "bad_numeric_format"
        if "non-negative" in value_format and d < Decimal("0"):
            return "negative_decimal"
        rng = spec.get("numeric_range")
        if rng and not (Decimal(str(rng[0])) <= d <= Decimal(str(rng[1]))):
            return "decimal_out_of_range"
        return None
    if value_format == "Date/Time" or data_type == "Date/Time":
        return None if _is_utc_datetime(value) else "bad_datetime"
    if value_format == "Currency":
        return None if value in _iso_4217() else "bad_currency"
    if value_format == "Allowed Values":
        allowed = spec.get("allowed_values")
        if allowed is not None and value not in allowed:
            return "not_in_allowed_values"
        return None
    if value_format == "Unit":
        if value != value.strip() or not value or _decimal_or_none(value) is not None:
            return "bad_unit"
        return None
    if value_format in ("JSON Object", "Key-Value") or data_type == "JSON":
        return _validate_json_column(column, value, value_format)
    if value_format == "Expected Format":
        return None if re.search(r"\d", value) and re.search(r"[A-Za-z]", value) \
            else "bad_expected_format"
    return None


# --------------------------------------------------------------------------- #
# Cross-field (single-row, SEMANTIC) rules — each returns (column, rule, message) tuples.
# --------------------------------------------------------------------------- #
def _cost_and_usage(
    row: dict, model: dict, supported: frozenset[str], absent: frozenset[str] = frozenset()
) -> list[tuple]:
    """Single-row Cost and Usage rules; ``absent`` are columns the source did not carry."""

    def empty(col: str) -> bool:
        return not (row.get(col) or "").strip()

    out: list[tuple] = []
    charge = (row.get("ChargeCategory") or "").strip()
    charge_class = (row.get("ChargeClass") or "").strip()
    commit_status = (row.get("CommitmentDiscountStatus") or "").strip()
    non_correction_use = charge in ("Usage", "Purchase") and charge_class != "Correction"

    if charge == "Tax" and not empty("PricingCategory"):
        out.append(("PricingCategory", "must_be_null_for_tax",
                    "PricingCategory must be null when ChargeCategory is 'Tax'"))
    for col in ("SkuId", "SkuPriceId"):
        if charge == "Tax" and not empty(col):
            out.append((col, "must_be_null_for_tax", f"{col} must be null for Tax charges"))

    # Condition-aware required columns (only when the provider declares support).
    if non_correction_use and COND_MULTIPLE_PRICING_CATEGORIES in supported and empty(
        "PricingCategory"
    ):
        out.append(("PricingCategory", "required_for_usage_or_purchase",
                    "PricingCategory required for Usage/Purchase (multiple pricing categories)"))
    if non_correction_use and COND_UNIT_PRICING in supported:
        for col in ("SkuId", "SkuPriceId"):
            if empty(col):
                out.append((col, "required_for_usage_or_purchase",
                            f"{col} required for Usage/Purchase when unit pricing is supported"))

    consumption_applies = charge == "Usage" and commit_status != "Unused"
    for col in ("ConsumedQuantity", "ConsumedUnit"):
        if not consumption_applies and not empty(col):
            out.append((col, "consumption_not_applicable",
                        f"{col} is only valid for Usage with status != 'Unused'"))
    if empty("ConsumedQuantity") and not empty("ConsumedUnit"):
        out.append(("ConsumedUnit", "unit_without_quantity",
                    "ConsumedUnit must be null when ConsumedQuantity is null"))
    if empty("SkuPriceId") and not empty("SkuPriceDetails"):
        out.append(("SkuPriceDetails", "details_without_sku_price_id",
                    "SkuPriceDetails must be null when SkuPriceId is null"))
    if empty("CommitmentDiscountQuantity") and not empty("CommitmentDiscountUnit"):
        out.append(("CommitmentDiscountUnit", "unit_without_quantity",
                    "CommitmentDiscountUnit must be null when CommitmentDiscountQuantity is null"))
    # The other direction of the unit/quantity pairing (CAU-*Unit-C-005/006).
    if empty("PricingQuantity") and not empty("PricingUnit"):
        out.append(("PricingUnit", "unit_without_quantity",
                    "PricingUnit must be null when PricingQuantity is null"))
    for quantity_col, unit_col in _UNIT_OF_QUANTITY:
        if not empty(quantity_col) and empty(unit_col):
            out.append((unit_col, "quantity_without_unit",
                        f"{unit_col} must not be null when {quantity_col} is not null"))

    def amount(col: str) -> Decimal | None:
        return _decimal_or_none((row.get(col) or "").strip())

    # CAU-EffectiveCost-C-017: EffectiveCost MUST equal BilledCost for Tax and Credit.
    if charge in ("Tax", "Credit"):
        billed, effective = amount("BilledCost"), amount("EffectiveCost")
        if billed is not None and effective is not None and billed != effective:
            out.append(("EffectiveCost", "effective_cost_differs_from_billed",
                        f"EffectiveCost must equal BilledCost when ChargeCategory is '{charge}'"))

    # CAU-ListCost-C-011 / CAU-ContractedCost-C-011: cost = unit price x PricingQuantity
    # whenever both are present (Correction rows included since 1.3).
    quantity = amount("PricingQuantity")
    if quantity is not None:
        for cost_col, price_col in (("ListCost", "ListUnitPrice"),
                                    ("ContractedCost", "ContractedUnitPrice")):
            price, cost = amount(price_col), amount(cost_col)
            if price is None or cost is None:
                continue
            if not _exactly_computable(price, quantity, cost):
                out.append((cost_col, "cost_identity_not_computable",
                            f"{price_col} x PricingQuantity cannot be computed exactly "
                            f"(an exponent lies beyond +/-{_MAX_EXPONENT})"))
                continue
            try:
                difference = _EXACT.abs(_EXACT.subtract(_EXACT.multiply(price, quantity), cost))
                allowed = _EXACT.multiply(_PRODUCT_TOLERANCE, max(_EXACT.abs(cost), Decimal(1)))
                mismatch = difference > allowed
            except DecimalException:
                out.append((cost_col, "cost_identity_not_computable",
                            f"{price_col} x PricingQuantity cannot be computed exactly"))
                continue
            if mismatch:
                out.append((cost_col, "cost_not_unit_price_times_quantity",
                            f"{cost_col} must equal {price_col} x PricingQuantity"))

    if empty("SkuPriceId"):
        # The null cascade (C-009 to C-016) applies only to a provider that declares unit
        # pricing, SkuPriceId's presence condition: an undeclared condition is never evaluated.
        if COND_UNIT_PRICING in supported:
            for col in _NULL_WITHOUT_SKU_PRICE_ID:
                if not empty(col):
                    out.append((col, "must_be_null_without_sku_price_id",
                                f"{col} must be null when SkuPriceId is null"))
    else:
        # CAU-ListUnitPrice-C-013 / CAU-ContractedUnitPrice-C-015 carry no applicability
        # criteria in the v1.4 model, so they apply whatever conditions are declared, as in
        # the official validator. A unit-price column the source does not carry has not met
        # its own presence condition, so it is not evaluated.
        # The pricing-currency unit prices (C-012 of each) are not enforced: their presence
        # depends on public price lists or virtual currencies (model rules D-046-C/D-054-C),
        # which a row does not show, and the machine-readable condition of
        # CAU-PricingCurrencyContractedUnitPrice-C-012-C (SkuPriceId IS null) contradicts its
        # own text ("when SkuPriceId is not null") and C-011.
        for col in ("ListUnitPrice", "ContractedUnitPrice"):
            if col not in absent and empty(col):
                out.append((col, "required_with_sku_price_id",
                            f"{col} must not be null when SkuPriceId is not null"))

    # ChargeFrequency must not be Usage-Based for Purchase charges.
    if charge == "Purchase" and (row.get("ChargeFrequency") or "").strip() == "Usage-Based":
        out.append(("ChargeFrequency", "usage_based_frequency_on_purchase",
                    "ChargeFrequency must not be 'Usage-Based' when ChargeCategory is 'Purchase'"))

    # ServiceSubcategory must belong to its parent ServiceCategory.
    sub = (row.get("ServiceSubcategory") or "").strip()
    cat = (row.get("ServiceCategory") or "").strip()
    parents = model.get("service_subcategory_parents", {})
    if sub and sub in parents and parents[sub] != cat:
        out.append(("ServiceSubcategory", "wrong_parent_category",
                    f"ServiceSubcategory '{sub}' belongs to '{parents[sub]}', not '{cat}'"))
    return out


def _last_updated_rule(created: str, updated: str) -> Callable:
    def _rule(
        row: dict, model: dict, supported: frozenset[str], absent: frozenset[str] = frozenset()
    ) -> list[tuple]:
        c, u = (row.get(created) or "").strip(), (row.get(updated) or "").strip()
        if c and u and _DATETIME_RE.fullmatch(c) and _DATETIME_RE.fullmatch(u):
            if _parse_dt(u) < _parse_dt(c):
                return [(updated, "last_updated_before_created", f"{updated} is before {created}")]
        return []

    return _rule


def _contract_commitment_upfront(
    row: dict, model: dict, supported: frozenset[str], absent: frozenset[str] = frozenset()
) -> list[tuple]:
    pm = (row.get("ContractCommitmentPaymentModel") or "").strip()
    pct = _decimal_or_none((row.get("ContractCommitmentPaymentUpfrontPercentage") or "").strip())
    if not pm or pct is None:
        return []
    expected_ok = {
        "All Upfront": pct == Decimal("1"),
        "No Upfront": pct == Decimal("0"),
        "Partial Upfront": Decimal("0") < pct < Decimal("1"),
    }.get(pm)
    if expected_ok is False:
        return [(
            "ContractCommitmentPaymentUpfrontPercentage", "upfront_percentage_mismatch",
            f"upfront percentage {pct} is inconsistent with payment model '{pm}'",
        )]
    return []


_CROSS_FIELD: dict[str, list[Callable]] = {
    "Cost and Usage": [_cost_and_usage],
    "Billing Period": [_last_updated_rule("BillingPeriodCreated", "BillingPeriodLastUpdated")],
    "Contract Commitment": [
        _last_updated_rule("ContractCommitmentCreated", "ContractCommitmentLastUpdated"),
        _contract_commitment_upfront,
    ],
    "Invoice Detail": [_last_updated_rule("InvoiceDetailCreated", "InvoiceDetailLastUpdated")],
}


def check_column_value(dataset: str, column: str, value: str) -> str | None:
    """Format-check one non-empty value against the model spec of ``dataset.column``.

    Returns the violated rule name (as in the lint report) or ``None``. Used by the
    supplement validator so client-supplied facts obey the same format rules as converted
    data, plus the formats the specification recommends (``_RECOMMENDED_FORMATS``): a fact
    the toolkit publishes on a client's behalf follows the recommended form. An unknown
    column returns ``"unknown_column"``.
    """
    name = resolve_dataset(dataset)
    spec = load_model()["datasets"][name]["columns"].get(column)
    if spec is None:
        return "unknown_column"
    text = value.strip()
    if not text:
        return None
    recommended = _RECOMMENDED_FORMATS.get(column)
    if recommended is not None and not recommended.fullmatch(text):
        return "bad_expected_format"
    return _format_violation(spec, column, text)


def lint_focus_1_4_structure(
    dataset: str,
    rows: list[dict[str, str]],
    *,
    model: dict | None = None,
    supported_conditions: Iterable[str] | None = None,
    profile: CapabilityProfile | None = None,
    source_absent_columns: Iterable[str] | None = None,
) -> LintReport:
    """Structurally + semantically lint ``rows`` against the FOCUS 1.4 model.

    This is a linter, not a full conformance validator: a clean report asserts
    ``STRUCTURAL_VALID`` and ``SEMANTIC_VALID`` only (see :data:`LEVEL_CROSS_DATASET`
    / :data:`LEVEL_OFFICIAL`, which are never asserted here).

    ``supported_conditions`` (raw strings) and/or ``profile`` (a validated
    :class:`~focus_data_toolkit.model.capabilities.CapabilityProfile`) declare the
    FOCUS applicability conditions the provider supports; conditionally-required
    columns are enforced only for those conditions (default: none enforced, so
    sparse-but-valid rows pass — an undeclared condition is *not evaluated*).

    A model column missing from ``rows`` is treated as absent, so a rule that depends on a
    column's presence is not evaluated for it. ``source_absent_columns`` lets a producer that
    writes such a column all null (rather than omitting it, as this toolkit's converter does)
    name it as absent too.
    """
    name = resolve_dataset(dataset)
    model = model or load_model()
    supported = frozenset(supported_conditions or ())
    if profile is not None:
        supported |= profile.supported_conditions
    columns: dict = model["datasets"][name]["columns"]
    violations: list[Violation] = []

    def add(rule, message, column=None, row_index=None, level=LEVEL_STRUCTURAL):
        violations.append(Violation(name, rule, message, column, row_index, level))

    if not rows:
        add("empty_dataset", "no rows provided")
        return LintReport(name, 0, tuple(violations))

    present = set().union(*(set(r.keys()) for r in rows))
    for key in sorted(present):
        if key not in columns and not key.startswith("x_"):
            add("unknown_column", f"{key} is not a FOCUS 1.4 {name} column", key)
    absent = frozenset(c for c in columns if c not in present) | frozenset(
        source_absent_columns or ()
    )

    cross_field = _CROSS_FIELD.get(name, [])
    for i, row in enumerate(rows):
        for col, spec in columns.items():
            if col not in row:
                if spec.get("feature_level") == "Mandatory":
                    add("missing_mandatory_column", f"required column {col} absent", col, i)
                continue
            value = (row.get(col) or "").strip()
            if not value:
                if not spec.get("allows_nulls", True):
                    add("null_not_allowed", "value is null/empty", col, i)
                continue
            rule = _format_violation(spec, col, value)
            if rule:
                add(rule, f"invalid value {value!r}", col, i)
        for fn in cross_field:
            for col, rule, msg in fn(row, model, supported, absent):
                add(rule, msg, col, i, level=LEVEL_SEMANTIC)

    return LintReport(name, len(rows), tuple(violations))


def validate_focus_1_4(
    dataset: str,
    rows: list[dict[str, str]],
    *,
    model: dict | None = None,
    supported_conditions: Iterable[str] | None = None,
) -> LintReport:
    """Deprecated alias of :func:`lint_focus_1_4_structure`.

    This is a structural + semantic **linter**, not a full FOCUS 1.4 conformance
    validator; ``report.ok`` means the lint passed, not that the data is fully
    FOCUS-conformant.
    """
    warnings.warn(
        "validate_focus_1_4 is deprecated; use lint_focus_1_4_structure. It is a "
        "structural + semantic linter, not a full FOCUS 1.4 conformance validator.",
        DeprecationWarning,
        stacklevel=2,
    )
    return lint_focus_1_4_structure(
        dataset, rows, model=model, supported_conditions=supported_conditions
    )
