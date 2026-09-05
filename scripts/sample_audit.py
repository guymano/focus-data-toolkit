"""Independent assertions and statistics for the toolkit's synthetic fixtures.

Expected semantics live here, not in provider tables or generator helpers. These
closed-period and fleet assertions are not validation rules for arbitrary exports.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from decimal import Decimal as D

COSTS = ("BilledCost", "EffectiveCost", "ListCost", "ContractedCost", "PricingCurrencyEffectiveCost")
REQUIRED = (
    "ProviderName", "PublisherName", "InvoiceIssuerName", "BillingAccountId",
    "BillingPeriodStart", "BillingPeriodEnd", "ChargePeriodStart", "ChargePeriodEnd",
    "ChargeCategory", "ChargeFrequency", "BillingCurrency", "PricingCurrency",
    "ServiceName", "ServiceCategory", "ServiceSubcategory", *COSTS,
)
NAMESPACES = {"AmazonEC2": "ec2", "AmazonS3": "s3", "AmazonRDS": "rds",
              "AWSLambda": "lambda", "AmazonVPC": "ec2", "AmazonCloudWatch": "cloudwatch",
              "AmazonDynamoDB": "dynamodb", "AWSGlue": "glue"}
COMPUTE = {"aws": "AmazonEC2", "azure": "Virtual Machines", "gcp": "Compute Engine"}
# Independent expected fees: providers/{aws,azure,gcp}._SERVICES[0].unit_price_usd
# times engine.determinism.COMMIT_RATE times 500. Keep literals here so the audit
# detects an unintended pricing change instead of recomputing the same mistake.
FEE = {"aws": D("32.016"), "azure": D("64.032"), "gcp": D("44.689")}
SKU_KEYS = {"StorageClass", "Redundancy", "CoreCount", "MemorySize", "InstanceType",
            "InstanceSeries", "OperatingSystem", "DiskType", "DiskSpace", "DiskMaxIops",
            "GpuCount", "NetworkMaxIops", "NetworkMaxThroughput"}


def audit(rows: list[dict[str, str]], provider: str, version: str,
          contracts: list[dict[str, str]] | None = None) -> None:
    """Raise AssertionError for a semantic defect; useful on deliberate corruptions too."""
    balances: dict[tuple[str, ...], D] = defaultdict(D)
    groups: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    taxed: set[int] = set()
    sku_for_offer: dict[tuple[str, ...], str] = {}
    offer_for_sku: dict[str, tuple[str, ...]] = {}
    price_for_id: dict[str, tuple[str, ...]] = {}
    id_for_price: dict[tuple[str, ...], str] = {}
    cc = {r["ContractCommitmentId"]: r for r in contracts or []}
    assert len(cc) == len(contracts or []), "duplicate commitment"
    spans: dict[str, tuple[str, str]] = {}
    for c in cc.values():
        span = c["ContractPeriodStart"], c["ContractPeriodEnd"]
        assert spans.setdefault(c["ContractId"], span) == span, "parent period"
        assert span[0] <= c["ContractCommitmentPeriodStart"] < c["ContractCommitmentPeriodEnd"] <= span[1]
        if c["ContractCommitmentCategory"] == "Usage":
            assert "." in c["ContractCommitmentQuantity"], "decimal quantity"
    for i, row in enumerate(rows, 1):
        assert all(row.get(k) for k in REQUIRED), ("required", i)
        assert row["ChargePeriodStart"] < row["ChargePeriodEnd"], "charge period"
        assert row["PricingCurrency"] in ("USD", "EUR")
        assert D(row["PricingCurrencyEffectiveCost"]) == D(row["EffectiveCost"]) * (
            D("0.92") if row["PricingCurrency"] == "EUR" else 1), "FX precision"
        key = tuple(row[k] for k in ("ProviderName", "BillingAccountId", "SubAccountId",
                                    "BillingPeriodStart", "BillingPeriodEnd", "BillingCurrency"))
        balances[key] += D(row["BilledCost"]) - D(row["EffectiveCost"])
        if row["ChargeCategory"] == "Purchase" and not row["CommitmentDiscountId"]:
            assert D(row["EffectiveCost"]) == D(row["BilledCost"]), "subscription"
            assert row["SkuMeter"] == "Subscription", "subscription offer"
        for cost, unit in (("ListCost", "ListUnitPrice"), ("ContractedCost", "ContractedUnitPrice")):
            if row[unit]:
                assert D(row[cost]) == D(row[unit]) * D(row["PricingQuantity"]), cost
        if row["ChargeCategory"] == "Tax":
            match = re.fullmatch(r"Synthetic tax 10% on usage record (\d+): .+", row["ChargeDescription"])
            assert match, "tax lineage"
            number = int(match[1])
            assert 1 <= number < i and number not in taxed, "tax source"
            taxed.add(number)
            source = rows[number - 1]
            assert source["ChargeCategory"] == "Usage" and source["PricingCategory"] == "Standard"
            for k in (*REQUIRED[:7], "SubAccountId", "InvoiceId", "ChargePeriodStart",
                      "ChargePeriodEnd", "BillingCurrency", "PricingCurrency", "ServiceName"):
                assert row[k] == source[k], ("tax identity", k)
            assert all(D(row[k]) == D(source[k]) / 10 for k in COSTS), "tax amount"
            assert not any(row[k] for k in ("ResourceId", "SkuId", "PricingQuantity",
                                           "PricingUnit", "ConsumedQuantity", "ListUnitPrice"))
        if row["SkuId"]:
            details = json.loads(row["SkuPriceDetails"])
            assert all(k in SKU_KEYS or (k.startswith("x_") and k[2:] not in SKU_KEYS) for k in details)
            offer = tuple(row[k] for k in ("ProviderName", "ServiceName", "SkuMeter", "RegionId", "PricingUnit")) + (json.dumps(details, sort_keys=True),)
            assert sku_for_offer.setdefault(offer, row["SkuId"]) == row["SkuId"], "SKU stability"
            assert offer_for_sku.setdefault(row["SkuId"], offer) == offer, "SKU collision"
            price = (row["SkuId"], row["BillingCurrency"], row["PricingCurrency"],
                     str(D(row["ListUnitPrice"]).normalize()), str(D(row["PricingCurrencyListUnitPrice"]).normalize()))
            assert price_for_id.setdefault(row["SkuPriceId"], price) == price, "price stability"
            assert id_for_price.setdefault(price, row["SkuPriceId"]) == row["SkuPriceId"], "price identity"
            if row["ServiceCategory"] == "Storage" and row["SkuMeter"] != "Subscription":
                assert details["StorageClass"] == ("Hot" if provider == "azure" else "Standard")
                if provider != "gcp":
                    assert details["Redundancy"] == ("Local" if provider == "azure" else "Zonal")
        if provider == "aws" and row["ResourceId"] and not row["CommitmentDiscountId"]:
            arn = row["ResourceId"].split(":", 5)
            assert len(arn) == 6 and arn[2] == NAMESPACES[row["ServiceName"]] and arn[4] == row["SubAccountId"], "AWS owner"
        if provider == "aws" and row.get("AllocatedResourceId"):
            assert row["AllocatedResourceId"].split(":")[4] == row["SubAccountId"], "allocated owner"
        if row["CommitmentDiscountId"]:
            groups[(row["CommitmentDiscountId"], row["ChargePeriodStart"], row["ChargePeriodEnd"])].append(row)
        if version == "1.3" and row["ContractApplied"]:
            elements = json.loads(row["ContractApplied"], parse_float=D)["Elements"]
            for el in elements:
                c = cc[el["ContractCommitmentId"]]
                assert c["ContractId"] == el["ContractId"], "contract parent"
                assert c["ContractCommitmentPeriodStart"] <= row["ChargePeriodStart"] < row["ChargePeriodEnd"] <= c["ContractCommitmentPeriodEnd"]
                if c["ContractCommitmentCategory"] == "Usage":
                    assert "ContractCommitmentAppliedCost" not in el, "usage branch"
                    assert el["ContractCommitmentAppliedUnit"] == c["ContractCommitmentUnit"], "contract unit"
                else:
                    assert "ContractCommitmentAppliedQuantity" not in el and "ContractCommitmentAppliedUnit" not in el
                if not row["CommitmentDiscountId"]:
                    kind = "USAGEMIN" if row["ServiceName"] == COMPUTE[provider] else "RATECARD" if row["ServiceCategory"] == "Storage" else "MINSPEND"
                    assert el["ContractCommitmentId"] == f"CC-{kind}-{provider.upper()}", "eligibility"
        if version == "1.3" and row["ChargeCategory"] == "Usage" and row["PricingCategory"] == "Standard":
            ratio = D("0.90") if row["ContractApplied"] else D(1)
            expected = (D(row["ListUnitPrice"]) * ratio).quantize(D("0.0000000001"))
            assert D(row["ContractedUnitPrice"]) == expected, "negotiated price"
    assert not any(balances.values()), "closed-period reconciliation"
    for (cid, _, _), group in groups.items():
        purchases = [r for r in group if r["ChargeCategory"] == "Purchase"]
        used = [r for r in group if r["CommitmentDiscountStatus"] == "Used"]
        unused = [r for r in group if r["CommitmentDiscountStatus"] == "Unused"]
        assert len(purchases) == 1 and len(used) == 2 and len(unused) == 1, "whole commitment block"
        p = purchases[0]
        assert D(p["BilledCost"]) == FEE[provider] and D(p["EffectiveCost"]) == 0, "fleet fee"
        assert sum(D(r["EffectiveCost"]) for r in used + unused) == D(p["BilledCost"]), "amortization"
        assert all(D(r["BilledCost"]) == 0 for r in used + unused)
        for r in used:
            assert r["ResourceType"] == "Compute Fleet" and r["ResourceId"].startswith(f"urn:focus-sample:{provider}:{r['RegionId']}:{r['SubAccountId']}:compute-fleet:")
            assert json.loads(r["Tags"])["SyntheticFleetSize"] == 500
            assert D(r["EffectiveCost"]) < D(r["ContractedCost"]) <= D(r["ListCost"])
        assert len({r["ResourceId"] for r in used}) == 1, "stable fleet"
        used_quantity = sum(D(r["ConsumedQuantity"]) for r in used)
        remaining = D(unused[0]["PricingQuantity"]) if p["CommitmentDiscountCategory"] == "Usage" else D(unused[0]["EffectiveCost"]) / (FEE[provider] / 500)
        assert used_quantity + remaining == 500, "capacity conservation"
        if contracts is not None:
            assert D(cc[cid]["ContractCommitmentCost"]) == FEE[provider] * 8760
            if p["CommitmentDiscountCategory"] == "Usage":
                assert D(cc[cid]["ContractCommitmentQuantity"]) == 4380000


def statistics(rows: list[dict[str, str]], provider: str) -> dict:
    def total(selected, column):
        return sum((D(r[column]) for r in selected), D(0))

    billed = total(rows, "BilledCost")
    purchased = total([r for r in rows if r["ChargeCategory"] == "Purchase" and r["CommitmentDiscountId"]], "BilledCost")
    used = total([r for r in rows if r["CommitmentDiscountStatus"] == "Used"], "EffectiveCost")
    unused = total([r for r in rows if r["CommitmentDiscountStatus"] == "Unused"], "EffectiveCost")
    eligible = [r for r in rows if r["ChargeCategory"] == "Usage" and r["ServiceName"] == COMPUTE[provider] and r["CommitmentDiscountStatus"] != "Unused"]
    covered = total([r for r in eligible if r["CommitmentDiscountStatus"] == "Used"], "ListCost")
    def ratio(a, b):
        return str(a / b) if b else None

    return {"rows": len(rows), "billed_cost": str(billed), "effective_cost": str(total(rows, "EffectiveCost")),
            "commitment_billed_cost": str(purchased), "commitment_share": ratio(purchased, billed),
            "utilization": ratio(used, purchased), "waste": ratio(unused, purchased),
            "compute_coverage": ratio(covered, total(eligible, "ListCost")),
            "contract_applied_rows": sum(bool(r.get("ContractApplied")) for r in rows)}
