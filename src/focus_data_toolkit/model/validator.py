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
  payment model, condition-aware required columns, ContractApplied deep structure).

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
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from focus_data_toolkit.model.focus_json_keys import (
    XPREFIX_ENFORCED_ELEMENTS_COLUMNS,
    XPREFIX_ENFORCED_KEYVALUE_COLUMNS,
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


def _no_dup_pairs(pairs: list[tuple[str, object]]) -> dict:
    seen: dict = {}
    for key, value in pairs:
        if key in seen:
            raise _DuplicateKey(key)
        seen[key] = value
    return seen


def _load_json_object(value: str) -> tuple[dict | None, str | None]:
    try:
        obj = json.loads(value, object_pairs_hook=_no_dup_pairs)
    except _DuplicateKey:
        return None, "duplicate_json_key"
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
        return _validate_contract_applied(value)
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
def _cost_and_usage(row: dict, model: dict, supported: frozenset[str]) -> list[tuple]:
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
    def _rule(row: dict, model: dict, supported: frozenset[str]) -> list[tuple]:
        c, u = (row.get(created) or "").strip(), (row.get(updated) or "").strip()
        if c and u and _DATETIME_RE.fullmatch(c) and _DATETIME_RE.fullmatch(u):
            if _parse_dt(u) < _parse_dt(c):
                return [(updated, "last_updated_before_created", f"{updated} is before {created}")]
        return []

    return _rule


def _contract_commitment_upfront(row: dict, model: dict, supported: frozenset[str]) -> list[tuple]:
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


def lint_focus_1_4_structure(
    dataset: str,
    rows: list[dict[str, str]],
    *,
    model: dict | None = None,
    supported_conditions: Iterable[str] | None = None,
) -> LintReport:
    """Structurally + semantically lint ``rows`` against the FOCUS 1.4 model.

    This is a linter, not a full conformance validator: a clean report asserts
    ``STRUCTURAL_VALID`` and ``SEMANTIC_VALID`` only (see :data:`LEVEL_CROSS_DATASET`
    / :data:`LEVEL_OFFICIAL`, which are never asserted here).

    ``supported_conditions`` declares the FOCUS applicability conditions the provider
    supports; conditionally-required columns are enforced only for those conditions
    (default: none enforced, so sparse-but-valid rows pass).
    """
    name = resolve_dataset(dataset)
    model = model or load_model()
    supported = frozenset(supported_conditions or ())
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
            for col, rule, msg in fn(row, model, supported):
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
