"""focus-toolkit command line interface.

Subcommands:

* ``generate`` — emit provider-realistic FOCUS 1.2/1.3 sample CSVs.
* ``convert``  — convert a FOCUS 1.2/1.3 source into the four FOCUS 1.4 datasets.
* ``validate`` — validate a CSV against the built-in FOCUS 1.4 model, or run
  the official FinOps validator (``--official``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from focus_data_toolkit.convert import (
    ConversionError,
    convert_to_focus_1_4,
    read_csv_rows,
    write_result,
)
from focus_data_toolkit.generators import FOCUS_VERSIONS, PROVIDERS, get_generator
from focus_data_toolkit.manifest import render as render_manifest
from focus_data_toolkit.model.validator import lint_focus_1_4_structure, resolve_dataset
from focus_data_toolkit.modes import Mode


def _cmd_generate(args: argparse.Namespace) -> int:
    module = get_generator(args.provider, args.focus_version)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = args.focus_version.replace(".", "_")

    cau = out_dir / f"focus_{suffix}_cost_and_usage_{args.provider}.csv"
    cau.write_bytes(module.generate_csv_bytes(args.rows, args.seed))
    print(f"wrote {cau} ({args.rows} rows, seed {args.seed})")

    if args.focus_version == "1.3":
        cc = out_dir / f"focus_{suffix}_contract_commitment_{args.provider}.csv"
        cc.write_bytes(module.generate_contract_commitment_csv_bytes(args.rows, args.seed))
        print(f"wrote {cc}")
    return 0


def _cmd_convert(args: argparse.Namespace) -> int:
    mode = Mode(args.mode)
    cau_rows = read_csv_rows(args.cost_and_usage)
    cc_rows = read_csv_rows(args.contract_commitment) if args.contract_commitment else None
    try:
        result = convert_to_focus_1_4(
            cau_rows,
            cc_rows,
            source_version=args.source_version,
            source_dataset=args.source_dataset,
            mode=mode,
            validate=not args.no_validate,
        )
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    written = write_result(result, args.out)
    if args.manifest:
        Path(args.manifest).write_text(render_manifest(result.manifest), encoding="utf-8")
    confidence = result.detection.confidence if result.detection else "?"
    print(
        f"source detected: FOCUS {result.source_version} "
        f"(dataset {result.detection.dataset if result.detection else '?'}, "
        f"confidence {confidence}, mode: {mode})"
    )
    for diag in result.diagnostics:
        print(f"note {diag.code}: {diag.message}", file=sys.stderr)
    for path in written:
        print(f"wrote {path}")

    for name in result.not_produced:
        entry = result.manifest["datasets"][name]
        print(f"not produced [{name}]: {entry.get('reason', 'unavailable')}")

    if mode is Mode.SYNTHETIC and result.assumptions_present:
        print(
            "WARNING: synthetic mode — datasets marked PRODUCED_SYNTHETIC contain ASSUMED "
            "values and are NOT fully FOCUS-conformant. See the manifest.",
            file=sys.stderr,
        )

    failed = False
    if not args.no_validate:
        for name, report in result.reports.items():
            status = "lint OK" if report.ok else f"{len(report.violations)} violation(s)"
            print(f"lint [{name}]: {status}")
            if not report.ok:
                failed = True
                for message in report.messages()[:20]:
                    print(f"  {message}")

    if failed:
        return 1
    if mode is Mode.SYNTHETIC and result.assumptions_present:
        return 4
    if mode is Mode.STRICT and result.not_produced:
        return 3
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    if args.official:
        from focus_data_toolkit.official_validator import run_official_validator

        if not args.focus_version:
            print("--official requires --focus-version (e.g. 1.2.0.1)", file=sys.stderr)
            return 2
        return run_official_validator(args.file, args.focus_version)

    rows = read_csv_rows(args.file)
    report = lint_focus_1_4_structure(resolve_dataset(args.dataset.replace("-", " ")), rows)
    status = (
        f"structural+semantic lint OK ({', '.join(report.levels_passed)})"
        if report.ok
        else f"{len(report.violations)} violation(s)"
    )
    print(f"{args.file}: {status}")
    if report.ok:
        print("  note: structural lint only — not a full FOCUS 1.4 conformance check")
    for message in report.messages()[:50]:
        print(f"  {message}")
    return 0 if report.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="focus-toolkit",
        description="Generate FOCUS 1.2/1.3 sample data, convert it to FOCUS 1.4, validate it.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="generate provider-realistic FOCUS 1.2/1.3 CSVs")
    gen.add_argument("--provider", choices=PROVIDERS, required=True)
    gen.add_argument("--focus-version", choices=FOCUS_VERSIONS, required=True)
    gen.add_argument("--rows", type=int, default=1000)
    gen.add_argument("--seed", type=int, default=1202)
    gen.add_argument("--out", default="out", help="output directory (default: ./out)")
    gen.set_defaults(func=_cmd_generate)

    conv = sub.add_parser("convert", help="convert FOCUS 1.2/1.3 towards the FOCUS 1.4 datasets")
    conv.add_argument("--cost-and-usage", required=True, help="FOCUS 1.2/1.3 Cost and Usage CSV")
    conv.add_argument(
        "--contract-commitment", help="optional FOCUS 1.3 Contract Commitment CSV (13 columns)"
    )
    conv.add_argument("--out", default="focus-1.4", help="output directory (default: ./focus-1.4)")
    conv.add_argument(
        "--source-version",
        help="force the FOCUS source version (1.2 or 1.3) instead of auto-detecting it",
    )
    conv.add_argument(
        "--source-dataset",
        help="force the source dataset (e.g. cost-and-usage) instead of auto-detecting it",
    )
    conv.add_argument(
        "--mode",
        choices=[m.value for m in Mode],
        default=Mode.STRICT.value,
        help="strict (default): never invent provider facts; synthetic: generate assumed "
        "values for demos/tests (labelled synthetic, not fully conformant)",
    )
    conv.add_argument(
        "--manifest", help="also write the conversion manifest JSON to this path"
    )
    conv.add_argument(
        "--no-validate", action="store_true", help="skip the built-in FOCUS 1.4 structural lint"
    )
    conv.set_defaults(func=_cmd_convert)

    val = sub.add_parser("validate", help="validate a CSV file")
    val.add_argument("file", help="CSV file to validate")
    val.add_argument(
        "--dataset",
        default="Cost and Usage",
        help="FOCUS 1.4 dataset name for the built-in validator "
        "(cost-and-usage, contract-commitment, billing-period, invoice-detail)",
    )
    val.add_argument(
        "--official", action="store_true", help="run the official FinOps focus-validator instead"
    )
    val.add_argument(
        "--focus-version", help="rule-model version for --official (e.g. 1.2.0.1)"
    )
    val.set_defaults(func=_cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
