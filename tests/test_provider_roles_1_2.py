"""FOCUS 1.2 participating entities: ServiceProviderName / HostProviderName (owner decision D-4).

Official sources, quoted in docs/conversion-rules.md:

* FOCUS 1.2 appendix "Origination of Cost Data": a marketplace purchase has Provider = the
  cloud provider and Publisher = the seller (3.1-3.3); cloud services bought through an MSP
  have Provider = the MSP and Publisher = the cloud provider (2.1).
* FOCUS 1.3: ServiceProviderName was introduced as the replacement for ProviderName.
* FOCUS 1.4 ServiceProviderName: in marketplace scenarios the Service Provider is the seller;
  appendix "Participating Entity Identification" 2.1-2.2 (MSP) and 3.1.1-3.3.2 (seller).
* FOCUS 1.4 HostProviderName: it MUST match ServiceProviderName unless the customer selected
  the host or the service provider exposes it, which a 1.2 source never does.

A row alone cannot tell the marketplace case from the MSP case, so the caller declares who
issued the file; only official FOCUS columns are read.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from focus_data_toolkit.convert import (
    ConversionError,
    convert_files,
    convert_to_focus_1_4,
    write_result,
)

CU = "Cost and Usage"


@pytest.fixture
def base_row(source_tables) -> dict[str, str]:
    cau, _ = source_tables[("aws", "1.2")]
    return dict(next(r for r in cau if r["ChargeCategory"] == "Usage"))


def _rows(base: dict[str, str], *pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [dict(base, ProviderName=provider, PublisherName=publisher)
            for provider, publisher in pairs]


def _entities(result) -> list[tuple[str, str]]:
    return [(r["ServiceProviderName"], r["HostProviderName"]) for r in result.datasets[CU]]


def _one(result, code: str):
    [diag] = [d for d in result.diagnostics if d.code == code]
    return diag


def _codes(result) -> set[str]:
    return {d.code for d in result.diagnostics}


MARKETPLACE = (("AWS", "AWS"), ("AWS", "Datadog"), ("AWS", "datadog "))


def test_without_a_role_provider_name_is_kept_and_the_rows_are_reported(base_row):
    result = convert_to_focus_1_4(_rows(base_row, *MARKETPLACE), mode="strict")
    assert _entities(result) == [("AWS", "AWS")] * 3
    diag = _one(result, "FDT-CTX-005")
    assert diag.severity.value == "WARNING"
    assert diag.context == {"rows_by_publisher": "Datadog:1; datadog:1"}
    # Nothing is refused: Cost and Usage is produced in strict mode.
    assert result.manifest["datasets"][CU]["status"] == "PRODUCED"
    rule = result.manifest["datasets"][CU]["columns"]["ServiceProviderName"]
    assert rule["source"] == "ProviderName"


def test_csp_role_names_the_marketplace_seller(base_row):
    result = convert_to_focus_1_4(
        _rows(base_row, *MARKETPLACE), mode="strict", provider_role="csp"
    )
    # The seller's own spelling is kept; the host follows the service provider.
    assert _entities(result) == [("AWS", "AWS"), ("Datadog", "Datadog"),
                                 ("datadog ", "datadog ")]
    diag = _one(result, "FDT-MIG-005")
    assert diag.severity.value == "INFO"
    assert diag.context == {"rows_by_publisher": "Datadog:1; datadog:1"}
    assert "FDT-CTX-005" not in _codes(result)
    columns = result.manifest["datasets"][CU]["columns"]
    assert columns["ServiceProviderName"]["source"] == (
        "ProviderName; PublisherName on Marketplace rows"
    )
    assert "declared provider role 'csp'" in columns["ServiceProviderName"]["note"]
    assert columns["HostProviderName"]["source"] == "ServiceProviderName"
    assert result.reports[CU].ok, result.reports[CU].messages()[:5]


def test_first_party_publishers_are_not_marketplace_sellers(base_row):
    rows = _rows(base_row, ("Microsoft Azure", "Microsoft"), ("Microsoft Azure", "Contoso"),
                 ("Microsoft Azure", "Microsoft Azure"))
    result = convert_to_focus_1_4(
        rows, mode="strict", provider_role="csp", first_party_publishers=[" microsoft "]
    )
    assert _entities(result) == [("Microsoft Azure", "Microsoft Azure"),
                                 ("Contoso", "Contoso"),
                                 ("Microsoft Azure", "Microsoft Azure")]
    assert _one(result, "FDT-MIG-005").context == {"rows_by_publisher": "Contoso:1"}
    note = result.manifest["datasets"][CU]["columns"]["ServiceProviderName"]["note"]
    assert "first-party publishers: microsoft" in note


def test_msp_role_keeps_the_msp_as_service_provider(base_row):
    rows = _rows(base_row, ("Contoso MSP", "AWS"), ("Contoso MSP", "Contoso MSP"))
    result = convert_to_focus_1_4(rows, mode="strict", provider_role="msp")
    assert _entities(result) == [("Contoso MSP", "Contoso MSP")] * 2
    assert not {"FDT-CTX-005", "FDT-MIG-005"} & _codes(result)
    note = result.manifest["datasets"][CU]["columns"]["ServiceProviderName"]["note"]
    assert "declared provider role 'msp'" in note


@pytest.mark.parametrize(
    ("role", "aliases", "match"),
    [("cloud", (), "unknown provider role"), (None, ("Microsoft",), "apply only"),
     ("msp", ("Microsoft",), "apply only")],
)
def test_an_invalid_declaration_is_refused(base_row, tmp_path, role, aliases, match):
    with pytest.raises(ConversionError, match=match):
        convert_to_focus_1_4([base_row], provider_role=role, first_party_publishers=aliases)
    path = _write(tmp_path / "cau.csv", [base_row])
    with pytest.raises(ConversionError, match=match):
        convert_files(path, tmp_path / "out", provider_role=role,
                      first_party_publishers=aliases)


def test_a_1_3_source_carries_its_own_service_provider(source_tables):
    cau, _ = source_tables[("aws", "1.3")]
    plain = convert_to_focus_1_4(cau, mode="strict")
    declared = convert_to_focus_1_4(cau, mode="strict", provider_role="csp")
    assert _entities(plain) == _entities(declared)
    assert not {"FDT-CTX-005", "FDT-MIG-005"} & _codes(declared)


def test_generated_sources(source_tables):
    # AWS names its provider and publisher alike; the generated Azure 1.2 sample names the
    # provider "Microsoft Azure" and the publisher "Microsoft", which a row cannot tell from a
    # marketplace seller until the publisher is declared first-party.
    aws, _ = source_tables[("aws", "1.2")]
    assert "FDT-CTX-005" not in _codes(convert_to_focus_1_4(aws, mode="strict"))
    azure, _ = source_tables[("azure", "1.2")]
    assert "FDT-CTX-005" in _codes(convert_to_focus_1_4(azure, mode="strict"))
    declared = convert_to_focus_1_4(
        azure, mode="strict", provider_role="csp", first_party_publishers=["Microsoft"]
    )
    assert not {"FDT-CTX-005", "FDT-MIG-005"} & _codes(declared)
    assert {sp for sp, _ in _entities(declared)} == {"Microsoft Azure"}


def _write(path: Path, rows: list[dict[str, str]]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.mark.parametrize("role", [None, "csp", "msp"])
def test_streaming_matches_eager(tmp_path, source_tables, role):
    cau, _ = source_tables[("azure", "1.2")]
    rows = [dict(r) for r in cau]
    rows[0]["PublisherName"] = "Contoso"
    aliases = ["Microsoft"] if role == "csp" else []
    src = _write(tmp_path / "cau.csv", rows)
    ref, streamed = tmp_path / "ref", tmp_path / "streamed"
    write_result(
        convert_to_focus_1_4(rows, mode="strict", provider_role=role,
                             first_party_publishers=aliases),
        ref,
    )
    convert_files(src, streamed, mode="strict", provider_role=role,
                  first_party_publishers=aliases)
    for name in sorted(p.name for p in ref.iterdir() if p.suffix == ".csv"):
        assert (ref / name).read_bytes() == (streamed / name).read_bytes(), name
    ref_manifest = json.loads((ref / "focus_1_4_manifest.json").read_text())
    assert ref_manifest == json.loads((streamed / "focus_1_4_manifest.json").read_text())


@pytest.mark.parametrize("stream", [False, True])
def test_cli_declares_the_role(tmp_path, base_row, stream, capsys):
    from focus_data_toolkit.cli import main

    rows = _rows(base_row, ("Microsoft Azure", "Microsoft"), ("Microsoft Azure", "Contoso"))
    src = _write(tmp_path / "cau.csv", rows)
    out = tmp_path / "out"
    # pipeline exit policy: the derived datasets are not produced from Cost and Usage alone.
    args = ["convert", "--cost-and-usage", str(src), "--out", str(out), "--exit-policy",
            "pipeline", "--provider-role", "csp", "--first-party-publisher", "Microsoft"]
    assert main([*args, "--stream"] if stream else args) == 0
    with open(out / "focus_1_4_cost_and_usage.csv", newline="", encoding="utf-8") as fh:
        service = [r["ServiceProviderName"] for r in csv.DictReader(fh)]
    assert service == ["Microsoft Azure", "Contoso"]
    # Both engines print the diagnostic code (the streaming one used to print "None").
    assert "note FDT-MIG-005:" in capsys.readouterr().err
