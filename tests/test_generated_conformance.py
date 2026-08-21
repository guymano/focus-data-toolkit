"""Internal conformance checks on the generated FOCUS 1.2/1.3 datasets.

Ports the upstream FOCUS-Sample-Data checker catalogue (24 assertions per provider for
1.2, 36-37 for 1.3 — ``generators/check_focus_1_2_samples.py`` /
``check_focus_1_3_samples.py`` in that repository) onto this toolkit's generator
output. Every check runs against both a freshly generated table (the shared
``source_tables`` fixture) and the committed golden fixtures, so the committed bytes
are proven conformant, not just reproducible. Upstream check numbers appear as
``[U-n]`` comments (1.3 numbering) for future syncs; byte reproducibility ([U-1],
[U-29]) is covered by ``tests/test_generator_golden.py`` and column counts ([U-2],
[U-30]) by ``tests/test_generators.py``.

All amount comparisons are exact ``Decimal`` equality — no tolerances.
"""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from focus_data_toolkit.convert.contract_applied import parse as parse_contract_applied
from focus_data_toolkit.generators import PROVIDERS, get_generator

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "compatibility_golden"

D = Decimal


def _dec_json(text: str) -> dict:
    return json.loads(text, parse_float=Decimal, parse_int=Decimal)


def _golden_rows(name: str) -> list[dict[str, str]]:
    data = (GOLDEN / name).read_bytes()
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


@pytest.fixture(scope="session")
def conformance_tables(source_tables):
    """{(source, provider, version): (cau, cc_or_None)} over generated + golden data."""
    tables = {}
    for provider in PROVIDERS:
        for version in ("1.2", "1.3"):
            tables[("generated", provider, version)] = source_tables[(provider, version)]
            tag = version.replace(".", "_")
            cau = _golden_rows(f"{provider}_{tag}_cost_and_usage_rows100_seed42.csv")
            cc = (
                _golden_rows(f"{provider}_{tag}_contract_commitment_rows100_seed42.csv")
                if version == "1.3"
                else None
            )
            tables[("golden", provider, version)] = (cau, cc)
    return tables


def _params():
    for source in ("generated", "golden"):
        for provider in PROVIDERS:
            for version in ("1.2", "1.3"):
                yield pytest.param(source, provider, version, id=f"{source}-{provider}-{version}")


def _params_1_3():
    for source in ("generated", "golden"):
        for provider in PROVIDERS:
            yield pytest.param(source, provider, id=f"{source}-{provider}")


matrix = pytest.mark.parametrize(("source", "provider", "version"), list(_params()))
matrix_1_3 = pytest.mark.parametrize(("source", "provider"), list(_params_1_3()))


def _commitment_rows(cau):
    return [r for r in cau if r["CommitmentDiscountId"]]


# --------------------------------------------------------------------------- #
# Cost arithmetic and price hierarchy
# --------------------------------------------------------------------------- #


@matrix
def test_costs_are_exact_products(conformance_tables, source, provider, version):
    # [U-3] ListCost == ListUnitPrice * PricingQuantity and
    # [U-4] ContractedCost == ContractedUnitPrice * PricingQuantity, exactly, on every
    # row that carries a unit price and a quantity.
    cau, _ = conformance_tables[(source, provider, version)]
    checked = 0
    for r in cau:
        if not (r["PricingQuantity"] and r["ListUnitPrice"]):
            continue
        checked += 1
        qty = D(r["PricingQuantity"])
        assert D(r["ListCost"]) == D(r["ListUnitPrice"]) * qty, r["ChargeDescription"]
        assert D(r["ContractedCost"]) == D(r["ContractedUnitPrice"]) * qty, r["ChargeDescription"]
    assert checked, "expected priced rows"


@matrix
def test_price_hierarchy_on_used_rows(conformance_tables, source, provider, version):
    # [U-17] ContractedCost differs from EffectiveCost on Used rows (the commitment
    # discount is NOT folded into the contracted price) and
    # [U-18] EffectiveCost < ContractedCost <= ListCost, strictly.
    cau, _ = conformance_tables[(source, provider, version)]
    used = [r for r in cau if r["CommitmentDiscountStatus"] == "Used"]
    assert used, "expected Used commitment rows"
    for r in used:
        assert D(r["EffectiveCost"]) < D(r["ContractedCost"]) <= D(r["ListCost"])


# --------------------------------------------------------------------------- #
# Commitment reconciliation and row properties
# --------------------------------------------------------------------------- #


@matrix
def test_commitment_reconciliation(conformance_tables, source, provider, version):
    # [U-5] per CommitmentDiscountId and billing period, and [U-6] per charge period:
    # sum(Usage EffectiveCost) == sum(Purchase BilledCost), under exact equality.
    cau, _ = conformance_tables[(source, provider, version)]
    per_period = defaultdict(lambda: [D(0), D(0)])
    per_id = defaultdict(lambda: [D(0), D(0)])
    for r in _commitment_rows(cau):
        keys = ((r["CommitmentDiscountId"], r["ChargePeriodStart"]), r["CommitmentDiscountId"])
        for bucket, key in zip((per_period, per_id), keys):
            if r["ChargeCategory"] == "Purchase":
                bucket[key][0] += D(r["BilledCost"])
            elif r["ChargeCategory"] == "Usage":
                bucket[key][1] += D(r["EffectiveCost"])
    assert per_id, "expected commitment rows"
    for bucket in (per_period, per_id):
        for key, (purchased, used) in bucket.items():
            assert purchased == used, key


@matrix
def test_commitment_purchase_rows(conformance_tables, source, provider, version):
    # [U-8] purchases exist; [U-9] Recurring; [U-10] PricingCategory Standard;
    # [U-11] ResourceId == CommitmentDiscountId; [U-12] EffectiveCost == 0. Plus the
    # explicit CommitmentDiscountQuantity/Unit semantics: committed spend in the
    # billing currency for Spend commitments, committed capacity in the native unit
    # for Usage commitments.
    cau, _ = conformance_tables[(source, provider, version)]
    purchases = [r for r in _commitment_rows(cau) if r["ChargeCategory"] == "Purchase"]
    assert purchases, "expected commitment purchase rows"
    for r in purchases:
        assert r["ChargeFrequency"] == "Recurring"
        assert r["PricingCategory"] == "Standard"
        assert r["ResourceId"] == r["CommitmentDiscountId"]
        assert D(r["EffectiveCost"]) == 0
        assert r["CommitmentDiscountQuantity"] and r["CommitmentDiscountUnit"]
        if r["CommitmentDiscountCategory"] == "Spend":
            assert D(r["CommitmentDiscountQuantity"]) == D(r["BilledCost"])
            assert r["CommitmentDiscountUnit"] == "USD"
        else:
            assert D(r["CommitmentDiscountQuantity"]) == D(r["PricingQuantity"])
            assert r["CommitmentDiscountUnit"] != "USD"


@matrix
def test_commitment_usage_status_partition(conformance_tables, source, provider, version):
    # [U-7] committed usage partitions into exactly Used + Unused, and both occur.
    cau, _ = conformance_tables[(source, provider, version)]
    statuses = {
        r["CommitmentDiscountStatus"]
        for r in _commitment_rows(cau)
        if r["ChargeCategory"] == "Usage"
    }
    assert statuses == {"Used", "Unused"}


@matrix
def test_unused_commitment_rows(conformance_tables, source, provider, version):
    # [U-13] Unused rows are Usage / Usage-Based / Committed, reference the commitment
    # itself and bill zero; [U-14] ConsumedQuantity/Unit are null; [U-15]
    # PricingQuantity, PricingUnit, SkuId, SkuPriceId are populated.
    cau, _ = conformance_tables[(source, provider, version)]
    unused = [r for r in cau if r["CommitmentDiscountStatus"] == "Unused"]
    assert unused, "expected Unused commitment rows"
    for r in unused:
        assert r["ChargeCategory"] == "Usage"
        assert r["ChargeFrequency"] == "Usage-Based"
        assert r["PricingCategory"] == "Committed"
        assert r["ResourceId"] == r["CommitmentDiscountId"]
        assert D(r["BilledCost"]) == 0
        assert not r["ConsumedQuantity"] and not r["ConsumedUnit"]
        assert r["PricingQuantity"] and r["PricingUnit"] and r["SkuId"] and r["SkuPriceId"]


@matrix
def test_used_commitment_rows_reference_consuming_resource(
    conformance_tables, source, provider, version
):
    # [U-16] Used rows reference the consuming resource (not the commitment) and carry
    # ConsumedQuantity/Unit.
    cau, _ = conformance_tables[(source, provider, version)]
    used = [r for r in cau if r["CommitmentDiscountStatus"] == "Used"]
    assert used
    for r in used:
        assert r["ResourceId"] != r["CommitmentDiscountId"]
        assert r["ConsumedQuantity"] and r["ConsumedUnit"]


@matrix
def test_commitment_discount_quantity_and_unit(conformance_tables, source, provider, version):
    # [U-19] CommitmentDiscountQuantity/Unit populated on every commitment
    # Usage/Purchase row; [U-20] the unit is "USD" if and only if the category is
    # "Spend".
    cau, _ = conformance_tables[(source, provider, version)]
    rows = [
        r for r in _commitment_rows(cau) if r["ChargeCategory"] in ("Usage", "Purchase")
    ]
    assert rows
    for r in rows:
        assert r["CommitmentDiscountQuantity"] and r["CommitmentDiscountUnit"]
        assert (r["CommitmentDiscountUnit"] == "USD") == (
            r["CommitmentDiscountCategory"] == "Spend"
        )


@matrix
def test_spend_commitment_rows_price_a_monetary_block(conformance_tables, source, provider, version):
    # A spend commitment prices a block of committed spend: on its Purchase and
    # Unused rows PricingUnit is the currency, the unit prices are exactly 1.00 and
    # the priced quantity IS the committed/unused spend (== CommitmentDiscountQuantity).
    # Used rows deliberately keep the consuming resource's native pricing instead.
    cau, _ = conformance_tables[(source, provider, version)]
    rows = [
        r
        for r in _commitment_rows(cau)
        if r["CommitmentDiscountCategory"] == "Spend"
        and (r["ChargeCategory"] == "Purchase" or r["CommitmentDiscountStatus"] == "Unused")
    ]
    assert rows, "expected spend commitment Purchase/Unused rows"
    for r in rows:
        assert r["PricingUnit"] == "USD"
        assert D(r["ListUnitPrice"]) == 1
        assert D(r["ContractedUnitPrice"]) == 1
        assert D(r["PricingQuantity"]) == D(r["CommitmentDiscountQuantity"])
        assert D(r["ListCost"]) == D(r["PricingQuantity"]) == D(r["ContractedCost"])


@matrix
def test_recurring_purchases_carry_no_upfront_metadata(conformance_tables, source, provider, version):
    # The per-charge-period model is a recurring, no-upfront fee: no Purchase row may
    # describe itself as all-upfront in its description, SKU details or discount name.
    cau, _ = conformance_tables[(source, provider, version)]
    purchases = [r for r in _commitment_rows(cau) if r["ChargeCategory"] == "Purchase"]
    assert purchases
    for r in purchases:
        assert r["ChargeFrequency"] == "Recurring"
        blob = " ".join(
            (r["ChargeDescription"], r["SkuPriceDetails"], r["CommitmentDiscountName"], r["SkuId"])
        ).lower()
        assert "allupfront" not in blob and "all upfront" not in blob
        details = json.loads(r["SkuPriceDetails"]) if r["SkuPriceDetails"] else {}
        if "x_PaymentOption" in details:
            assert details["x_PaymentOption"] == "NoUpfront"


@matrix
def test_resource_type_present_wherever_resource_id_is(conformance_tables, source, provider, version):
    # Pins the data side of validator rule ResourceType-C-005-C ("MUST NOT be null
    # when ResourceId is not null"): the engine flags null-ResourceId rows by
    # mis-applying the condition, but no generated row genuinely violates the rule.
    cau, _ = conformance_tables[(source, provider, version)]
    for r in cau:
        if r["ResourceId"]:
            assert r["ResourceType"], r["ResourceId"]


@matrix
def test_commitment_discount_status_only_on_commitment_usage(
    conformance_tables, source, provider, version
):
    # Pins the data side of the CommitmentDiscountStatus-C-003/004-C branch pair:
    # the status is set exactly on commitment Usage rows, null everywhere else.
    cau, _ = conformance_tables[(source, provider, version)]
    for r in cau:
        expected = bool(r["CommitmentDiscountId"]) and r["ChargeCategory"] == "Usage"
        assert bool(r["CommitmentDiscountStatus"]) == expected


# --------------------------------------------------------------------------- #
# Pricing nullability and currency
# --------------------------------------------------------------------------- #


@matrix
def test_pricing_quantity_unit_pairing(conformance_tables, source, provider, version):
    # [U-21] PricingUnit is null exactly when PricingQuantity is null; [U-22]
    # PricingQuantity populated on every Usage and Purchase row; Tax rows leave it
    # null (1.2 check #21).
    cau, _ = conformance_tables[(source, provider, version)]
    for r in cau:
        assert bool(r["PricingQuantity"]) == bool(r["PricingUnit"])
        if r["ChargeCategory"] in ("Usage", "Purchase"):
            assert r["PricingQuantity"]
        elif r["ChargeCategory"] == "Tax":
            assert not r["PricingQuantity"]


@matrix
def test_pricing_currency_on_tax_and_credit(conformance_tables, source, provider, version):
    # Toolkit port of the 1.3 behaviour into 1.2 (review item 4): Tax/Credit rows are
    # priced in the billing currency, so PricingCurrency and
    # PricingCurrencyEffectiveCost are populated and mirror EffectiveCost exactly.
    cau, _ = conformance_tables[(source, provider, version)]
    rows = [r for r in cau if r["ChargeCategory"] in ("Tax", "Credit")]
    assert rows, "expected Tax rows"
    for r in rows:
        assert r["PricingCurrency"] == "USD"
        assert D(r["PricingCurrencyEffectiveCost"]) == D(r["EffectiveCost"])


# --------------------------------------------------------------------------- #
# Billing identity
# --------------------------------------------------------------------------- #


@matrix
def test_billing_identity_uniqueness(conformance_tables, source, provider, version):
    # [U-23] each BillingAccountId maps to exactly one BillingAccountName and
    # [U-24] exactly one InvoiceId, across the whole dataset.
    cau, _ = conformance_tables[(source, provider, version)]
    names: dict[str, set[str]] = defaultdict(set)
    invoices: dict[str, set[str]] = defaultdict(set)
    for r in cau:
        names[r["BillingAccountId"]].add(r["BillingAccountName"])
        invoices[r["BillingAccountId"]].add(r["InvoiceId"])
    assert all(len(v) == 1 for v in names.values())
    assert all(len(v) == 1 for v in invoices.values())


@matrix
def test_commitment_group_billing_identity(conformance_tables, source, provider, version):
    # [U-25] every row of a commitment shares one billing identity.
    cau, _ = conformance_tables[(source, provider, version)]
    keys = (
        "BillingAccountId", "BillingAccountName", "BillingAccountType",
        "SubAccountId", "SubAccountName", "SubAccountType", "InvoiceId",
    )
    identity: dict[str, tuple[str, ...]] = {}
    for r in _commitment_rows(cau):
        current = tuple(r[k] for k in keys)
        assert identity.setdefault(r["CommitmentDiscountId"], current) == current


# --------------------------------------------------------------------------- #
# ContractApplied and the Contract Commitment dataset (1.3 only)
# --------------------------------------------------------------------------- #


@matrix_1_3
def test_contract_applied_structure_and_casing(conformance_tables, source, provider):
    # [U-26] elements carry the identifier keys and proper JSON number metric types;
    # [U-27] quantity/unit pairing; [U-28] at least one row populates ContractApplied.
    # Conformance demands the canonical Id casing of erratum #3 strictly — the legacy
    # ContractID casing the converter merely tolerates is a failure here.
    cau, _ = conformance_tables[(source, provider, "1.3")]
    populated = 0
    for r in cau:
        text = r["ContractApplied"]
        if not text:
            continue
        populated += 1
        legacy: set[str] = set()
        parse_contract_applied(text, version="1.3", legacy_sink=legacy)
        assert not legacy, f"legacy pre-erratum casing in generated data: {legacy}"
    assert populated


@matrix_1_3
def test_contract_applied_on_all_commitment_usage_rows(conformance_tables, source, provider):
    # Used and Unused rows alike are charges tied to the commitment, so both carry
    # ContractApplied, with one metric branch per category: a spend commitment
    # applies a cost alone, a usage commitment applies the measured quantity in its
    # native unit alone (so the quantity branch survives the 1.4 oneOf migration).
    cau, _ = conformance_tables[(source, provider, "1.3")]
    rows = [r for r in _commitment_rows(cau) if r["ChargeCategory"] == "Usage"]
    assert rows
    for r in rows:
        ca = parse_contract_applied(r["ContractApplied"], version="1.3")
        (element,) = ca.elements
        assert element.contract_commitment_id == r["CommitmentDiscountId"]
        if r["CommitmentDiscountCategory"] == "Spend":
            assert element.applied_cost is not None
            assert D(element.applied_cost) == D(r["EffectiveCost"])
            assert element.applied_quantity is None and element.applied_unit is None
        else:
            assert element.applied_cost is None
            assert element.applied_quantity is not None and element.applied_unit is not None
            assert D(element.applied_quantity) == D(r["PricingQuantity"])


@matrix_1_3
def test_contract_applied_on_commitment_purchase_rows(conformance_tables, source, provider):
    # The recurring Purchase is a charge tied to the commitment too: it carries
    # ContractApplied whose ContractCommitmentId equals ResourceId (rule
    # CAU-ContractAppliedObject-O-039-C), with the same one-branch-per-category
    # semantics — committed spend applies the fee, committed capacity the quantity.
    cau, _ = conformance_tables[(source, provider, "1.3")]
    purchases = [r for r in _commitment_rows(cau) if r["ChargeCategory"] == "Purchase"]
    assert purchases
    for r in purchases:
        ca = parse_contract_applied(r["ContractApplied"], version="1.3")
        (element,) = ca.elements
        assert element.contract_commitment_id == r["ResourceId"] == r["CommitmentDiscountId"]
        if r["CommitmentDiscountCategory"] == "Spend":
            assert element.applied_cost is not None
            assert D(element.applied_cost) == D(r["BilledCost"])
            assert element.applied_quantity is None
        else:
            assert element.applied_cost is None
            assert element.applied_quantity is not None
            assert D(element.applied_quantity) == D(r["PricingQuantity"])


@matrix_1_3
def test_contract_commitment_category_rules(conformance_tables, source, provider):
    # [U-31] Spend commitments carry ContractCommitmentCost (and no quantity/unit);
    # [U-32] Usage commitments carry ContractCommitmentQuantity + Unit;
    # [U-33] the contract period encloses the commitment period.
    _, cc = conformance_tables[(source, provider, "1.3")]
    assert cc
    for r in cc:
        if r["ContractCommitmentCategory"] == "Spend":
            assert r["ContractCommitmentCost"]
            assert not r["ContractCommitmentQuantity"] and not r["ContractCommitmentUnit"]
        else:
            assert r["ContractCommitmentQuantity"] and r["ContractCommitmentUnit"]
        assert (
            r["ContractPeriodStart"]
            < r["ContractCommitmentPeriodStart"]
            < r["ContractCommitmentPeriodEnd"]
            < r["ContractPeriodEnd"]
        )


@matrix_1_3
def test_cross_file_contract_applied_integrity(conformance_tables, source, provider):
    # [U-34] every ContractCommitmentId referenced from ContractApplied exists in the
    # Contract Commitment dataset; [U-35] the dataset carries at least one commitment
    # that is NOT a CommitmentDiscountId; [U-36] those non-discount commitments are
    # reachable through ContractApplied; [U-37] at least one ContractId carries
    # multiple commitments.
    cau, cc = conformance_tables[(source, provider, "1.3")]
    assert cc
    cc_ids = {r["ContractCommitmentId"] for r in cc}
    discount_ids = {r["CommitmentDiscountId"] for r in cau if r["CommitmentDiscountId"]}
    referenced: set[str] = set()
    for r in cau:
        if not r["ContractApplied"]:
            continue
        for element in parse_contract_applied(r["ContractApplied"], version="1.3").elements:
            referenced.add(element.contract_commitment_id)
    assert referenced <= cc_ids  # [U-34]
    non_discount = cc_ids - discount_ids
    assert non_discount  # [U-35]
    assert non_discount <= referenced  # [U-36]
    contracts: dict[str, set[str]] = defaultdict(set)
    for r in cc:
        contracts[r["ContractId"]].add(r["ContractCommitmentId"])
    assert any(len(ids) > 1 for ids in contracts.values())  # [U-37]


# --------------------------------------------------------------------------- #
# Split Cost Allocation groups (1.3 only; beyond the upstream catalogue)
# --------------------------------------------------------------------------- #


@matrix_1_3
def test_split_allocation_groups_conserve_each_cost_column(
    conformance_tables, source, provider
):
    # Ratios sum to exactly 1 and EVERY cost column is conserved separately: the group
    # total equals unit price x total quantity for ListCost, ContractedCost,
    # BilledCost and EffectiveCost alike (review item: per-column conservation, not a
    # single generic host cost).
    cau, _ = conformance_tables[(source, provider, "1.3")]
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for r in cau:
        if r["AllocatedMethodId"]:
            groups[(r["ResourceId"], r["ChargePeriodStart"])].append(r)
    assert groups, "expected split allocation groups"
    for key, rows in groups.items():
        assert len(rows) >= 2, key
        ratios = [
            _dec_json(r["AllocatedMethodDetails"])["Elements"][0]["AllocatedRatio"]
            for r in rows
        ]
        assert sum(ratios) == 1, key
        assert len({r["AllocatedMethodId"] for r in rows}) == 1
        assert len({r["AllocatedResourceId"] for r in rows}) == len(rows)
        list_unit = {r["ListUnitPrice"] for r in rows}
        contracted_unit = {r["ContractedUnitPrice"] for r in rows}
        assert len(list_unit) == 1 and len(contracted_unit) == 1
        total_qty = sum(D(r["PricingQuantity"]) for r in rows)
        for column, unit in (
            ("ListCost", list_unit),
            ("ContractedCost", contracted_unit),
            ("BilledCost", contracted_unit),
            ("EffectiveCost", contracted_unit),
        ):
            assert sum(D(r[column]) for r in rows) == D(next(iter(unit))) * total_qty, (
                key,
                column,
            )


# --------------------------------------------------------------------------- #
# Golden fixtures cover the same variants the graded checks assume
# --------------------------------------------------------------------------- #


def test_generated_and_golden_sources_agree_at_the_golden_grid():
    # The golden rows100_seed42 files must be exactly what the generators produce, so
    # the conformance runs over "golden" and freshly generated data cannot diverge
    # silently. (Byte equality itself is asserted in test_generator_golden.py; this
    # guards the conformance fixtures' provenance at the row level.)
    for provider in PROVIDERS:
        for version in ("1.2", "1.3"):
            module = get_generator(provider, version)
            tag = version.replace(".", "_")
            name = f"{provider}_{tag}_cost_and_usage_rows100_seed42.csv"
            generated = module.generate_csv_bytes(rows=100, seed=42)
            assert generated == (GOLDEN / name).read_bytes()
