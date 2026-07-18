"""focus-toolkit command line interface.

Subcommands:

* ``generate``    — emit provider-realistic FOCUS 1.2/1.3 sample CSVs.
* ``convert``     — convert a FOCUS 1.2/1.3 source (CSV or Parquet) into the four
  FOCUS 1.4 datasets, optionally completed by ``--supplement`` client facts.
* ``gaps``        — report exactly which facts a client must supply for the four
  FOCUS 1.4 datasets to be produced factually from a given source.
* ``supplements`` — pre-flight ``validate`` supplement files against a source, and
  list the provider-native export ``adapters`` (AWS / Azure / GCP).
* ``detect``      — detect the FOCUS dataset / version of a file's header.
* ``validate``    — validate a produced file against the built-in FOCUS 1.4 model,
  or run the official FinOps validator (``--official``).
* ``validate-bundle`` — run the cross-dataset validation gate over a bundle of
  FOCUS 1.4 datasets (explicit per-dataset files or an auto-detected directory).
* ``version``     — print the toolkit version and optional-extra availability.
* ``ui``          — launch the local Studio web UI (needs the ``[studio]`` extra;
  localhost-only by default).
* ``clean``       — recover interrupted publishes and remove leftover staging
  directories.

Exit codes (``convert``): 0 ok; 1 lint/bundle/write failure; 2 invalid input/args;
3 strict mode left datasets NOT_PRODUCED; 4 synthetic assumptions present; 5 disk
budget / free-space exhaustion; 130 cancelled (SIGINT/SIGTERM). ``--exit-policy
pipeline`` maps 3 and 4 to 0 for orchestrators.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import signal
import sys
import threading
from pathlib import Path

from focus_data_toolkit.convert import (
    OUTPUT_FORMATS,
    AtomicWriteError,
    ConversionCancelled,
    ConversionError,
    DestinationExistsError,
    OnExists,
    convert_files,
    convert_to_focus_1_4,
    write_result,
)
from focus_data_toolkit.generators import FOCUS_VERSIONS, PROVIDERS, get_generator
from focus_data_toolkit.io.parquet_io import COMPRESSIONS
from focus_data_toolkit.io.records import MalformedRecordError
from focus_data_toolkit.io.row_source import read_source_rows
from focus_data_toolkit.manifest import render as render_manifest
from focus_data_toolkit.model.capabilities import KNOWN_CONDITIONS, CapabilityProfile
from focus_data_toolkit.model.validator import lint_focus_1_4_structure, resolve_dataset
from focus_data_toolkit.modes import Mode
from focus_data_toolkit.runtime import ResourceLimitError, parse_size


def _parse_size(value: str | None) -> int | None:
    """Parse a byte size for --target-file-size (e.g. ``128MB``, ``512KB``, or a byte count)."""
    try:
        return parse_size(value)
    except ValueError as exc:
        raise ConversionError(
            f"invalid --target-file-size {value!r}: use e.g. 128MB, 512KB, or a byte count"
        ) from exc


def _apply_exit_policy(code: int, policy: str) -> int:
    """Map functional exit codes to a pipeline-friendly scheme when requested.

    In ``pipeline`` mode a functional-but-complete outcome — ``3`` (strict mode left some
    datasets NOT_PRODUCED) or ``4`` (synthetic assumptions present) — is reported as success
    (``0``), so orchestrators (Kubernetes / Airflow / Jenkins / AWS Batch), which treat any
    non-zero code as failure, do not mark a legitimate run failed. Genuine failures (``1`` /
    ``2`` / ``5`` / ``130``) stay non-zero. ``detailed`` (default) keeps the historic codes;
    the full functional status is always in the manifest and the ``_run.json`` sidecar.
    """
    if policy == "pipeline" and code in (3, 4):
        return 0
    return code


@contextlib.contextmanager
def _cancel_on_signals():
    """Yield a threading.Event set by SIGINT/SIGTERM, restoring prior handlers on exit.

    A batch run in a container receives SIGTERM on ``docker stop`` / pod eviction and SIGINT
    on Ctrl-C; turning both into a cooperative cancel lets the streaming engine unwind cleanly
    (staging removed, nothing published) instead of dying mid-write. Signal handlers can only
    be installed on the main thread, so off-main-thread callers (e.g. a server) silently get an
    event they can set themselves.
    """
    event = threading.Event()

    def _handler(_signum, _frame):
        event.set()

    previous: dict = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[sig] = signal.signal(sig, _handler)
        except (ValueError, OSError):  # not on the main thread
            pass
    try:
        yield event
    finally:
        for sig, prev in previous.items():
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, prev)


def _stderr_progress():
    """Return a ProgressCallback that renders a single throttled status line on stderr."""
    state = {"width": 0}

    def render(event) -> None:
        pct = "" if event.fraction is None else f" {event.fraction * 100:5.1f}%"
        total = "" if event.total is None else f"/{event.total:,}"
        message = f" - {event.message}" if event.message else ""
        line = f"{event.phase} {event.completed:,}{total} {event.unit}{pct}{message}"
        pad = max(0, state["width"] - len(line))
        state["width"] = len(line)
        sys.stderr.write("\r" + line + " " * pad)
        sys.stderr.flush()

    return render


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


def _read_header(path: str) -> tuple[str, ...]:
    """Read only the header of a CSV (gzip auto-detected) or Parquet file."""
    from focus_data_toolkit.io.row_source import open_row_source

    reader = open_row_source(path)
    try:
        return reader.source_columns
    finally:
        reader.close()


def _cmd_gaps(args: argparse.Namespace) -> int:
    from focus_data_toolkit.convert import _resolve_source_version
    from focus_data_toolkit.supplement import compute_gaps

    try:
        header = _read_header(args.cost_and_usage)
        version, _detection = _resolve_source_version(
            header,
            source_version=args.source_version,
            source_dataset=args.source_dataset,
            mode=Mode.STRICT,
        )
        cc_header = (
            _read_header(args.contract_commitment) if args.contract_commitment else None
        )
    except (ConversionError, MalformedRecordError) as exc:
        # MalformedRecordError: unreadable source header (malformed CSV, corrupt Parquet,
        # or the missing-pyarrow install hint) — a CLI error, not a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = compute_gaps(header, version, cc_columns=cc_header)
    payload = (
        json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n"
        if args.format == "json"
        else report.render_text()
    )
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(payload, end="")
    return 0


def _supplement_specs(args: argparse.Namespace) -> list:
    """Collect supplement file specs from --supplement / --supplements-dir."""
    from focus_data_toolkit.supplement import load_bundle_dir, parse_supplement_arg

    specs = [parse_supplement_arg(arg) for arg in (args.supplement or [])]
    if getattr(args, "supplements_dir", None):
        specs.extend(load_bundle_dir(args.supplements_dir))
    return specs


def _cmd_supplements_validate(args: argparse.Namespace) -> int:
    from focus_data_toolkit.supplement import (
        SupplementBundle,
        SupplementError,
        source_key_sets,
        validate_supplements,
    )
    from focus_data_toolkit.supplement.validate import has_blocking_errors

    try:
        specs = _supplement_specs(args)
        if not specs:
            print("error: provide --supplement and/or --supplements-dir", file=sys.stderr)
            return 2
        bundle = SupplementBundle.load(specs)
    except SupplementError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        cau_rows = read_source_rows(args.cost_and_usage)
        cc_rows = (
            read_source_rows(args.contract_commitment) if args.contract_commitment else None
        )
    except MalformedRecordError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    diagnostics = validate_supplements(bundle, source_key_sets(cau_rows, cc_rows))
    for diag in diagnostics:
        print(f"{diag.severity} {diag.code}: {diag.message}")
        for key, value in diag.context.items():
            print(f"    {key}: {value}")
    if has_blocking_errors(diagnostics):
        print("supplements NOT usable: fix the ERROR diagnostics above", file=sys.stderr)
        return 1
    kinds = ", ".join(sorted(bundle.tables)) or "-"
    print(f"supplements OK ({kinds}); see FDT-SUPP-010 entries for coverage")
    return 0


def _cmd_supplements_adapters(args: argparse.Namespace) -> int:
    from focus_data_toolkit.supplement.adapters import load_adapters

    adapters = load_adapters()
    if not adapters:
        print("no provider adapters available")
        return 0
    for name in sorted(adapters):
        a = adapters[name]
        print(f"{name} (v{a.version}) -> {a.target_kind}")
        print(f"    source: {a.provenance.get('source', '?')}")
        print(f"    doc:    {a.provenance.get('doc_url', '?')}")
    return 0


def _capabilities(args: argparse.Namespace) -> CapabilityProfile:
    """Build the capability profile from repeated ``--supports`` flags."""
    return CapabilityProfile(frozenset(args.supports), source="cli") if args.supports \
        else CapabilityProfile.none()


def _cmd_convert_stream(args: argparse.Namespace, mode: Mode) -> int:
    """Bounded-memory streaming conversion (required for Parquet output / large inputs)."""
    from focus_data_toolkit.supplement import SupplementError

    partition_by = [c.strip() for c in (args.partition_by or "").split(",") if c.strip()]
    progress = _stderr_progress() if getattr(args, "progress", False) else None
    try:
        target_file_size = _parse_size(args.target_file_size)
        with _cancel_on_signals() as cancel_event:
            out = convert_files(
                args.cost_and_usage,
                args.out,
                contract_commitment=args.contract_commitment,
                source_version=args.source_version,
                source_dataset=args.source_dataset,
                mode=mode,
                validate=not args.no_validate,
                on_exists=OnExists(args.on_exists),
                keep_temp=args.keep_temp,
                output_format=args.output_format,
                partition_by=partition_by,
                compression=args.compression,
                target_file_size=target_file_size,
                capabilities=_capabilities(args),
                supplements=_load_supplements(args),
                progress=progress,
                cancel=cancel_event.is_set,
            )
    except ConversionCancelled:
        # Subclass of ConversionError — must be caught first. Nothing was published.
        if progress:
            sys.stderr.write("\n")
        print("cancelled: no output written", file=sys.stderr)
        return 130
    except ResourceLimitError as exc:
        if progress:
            sys.stderr.write("\n")
        print(f"error: [{exc.diagnostic.code}] {exc.diagnostic.message}", file=sys.stderr)
        return 5
    except SupplementError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (ConversionError, DestinationExistsError, MalformedRecordError) as exc:
        # MalformedRecordError covers a malformed CSV record and a missing PyArrow (the clear
        # install hint) — surface both as a normal CLI error, not a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except AtomicWriteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if progress:
        sys.stderr.write("\n")

    from focus_data_toolkit.manifest import NOT_PRODUCED

    published_manifest = out / "focus_1_4_manifest.json"
    if args.manifest:  # honour --manifest for the streaming path too (parity with eager)
        Path(args.manifest).write_text(
            published_manifest.read_text(encoding="utf-8"), encoding="utf-8"
        )
    manifest = json.loads(published_manifest.read_text(encoding="utf-8"))
    for diag in manifest.get("diagnostics", []):
        print(f"note {diag.get('code')}: {diag.get('message')}", file=sys.stderr)
    print(f"wrote {out}/ (format {args.output_format}, mode {mode})")
    for name, entry in manifest["datasets"].items():
        if entry.get("status") == NOT_PRODUCED:
            print(f"not produced [{name}]: {entry.get('reason', 'unavailable')}")
    if mode is Mode.SYNTHETIC and manifest.get("assumptions_present"):
        print(
            "WARNING: synthetic mode — datasets marked PRODUCED_SYNTHETIC contain ASSUMED "
            "values and are NOT fully FOCUS-conformant. See the manifest.",
            file=sys.stderr,
        )
        return _apply_exit_policy(4, args.exit_policy)
    if mode is Mode.STRICT and any(
        e.get("status") == NOT_PRODUCED for e in manifest["datasets"].values()
    ):
        return _apply_exit_policy(3, args.exit_policy)
    return 0


def _load_supplements(args: argparse.Namespace):
    """Load the supplement bundle from CLI args (None when no supplements given)."""
    from focus_data_toolkit.supplement import SupplementBundle

    specs = _supplement_specs(args)
    return SupplementBundle.load(specs) if specs else None


def _cmd_convert(args: argparse.Namespace) -> int:
    mode = Mode(args.mode)
    # Parquet output, explicit --stream, or partitioning go through the bounded-memory streaming
    # engine; the eager path (rich per-dataset reporting) stays the default for CSV output.
    if args.output_format == "parquet" or args.stream or args.partition_by:
        return _cmd_convert_stream(args, mode)

    from focus_data_toolkit.supplement import SupplementError

    try:
        cau_rows = read_source_rows(args.cost_and_usage)
        cc_rows = (
            read_source_rows(args.contract_commitment) if args.contract_commitment else None
        )
        result = convert_to_focus_1_4(
            cau_rows,
            cc_rows,
            source_version=args.source_version,
            source_dataset=args.source_dataset,
            mode=mode,
            validate=not args.no_validate,
            capabilities=_capabilities(args),
            supplements=_load_supplements(args),
        )
    except SupplementError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (ConversionError, MalformedRecordError) as exc:
        # MalformedRecordError covers a malformed source record and a missing PyArrow
        # (its clear install hint) for Parquet input — a CLI error, not a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    confidence = result.detection.confidence if result.detection else "?"
    print(
        f"source detected: FOCUS {result.source_version} "
        f"(dataset {result.detection.dataset if result.detection else '?'}, "
        f"confidence {confidence}, mode: {mode})"
    )
    for diag in result.diagnostics:
        print(f"note {diag.code}: {diag.message}", file=sys.stderr)

    # Mandatory lint gate: a lint-failing result is never written to disk.
    if not args.no_validate and not result.ok:
        for name, report in result.reports.items():
            status = "lint OK" if report.ok else f"{len(report.violations)} violation(s)"
            print(f"lint [{name}]: {status}")
            for message in report.messages()[:20]:
                print(f"  {message}")
        print("output not written: mandatory lint failed", file=sys.stderr)
        return 1

    try:
        written = write_result(
            result, args.out, on_exists=OnExists(args.on_exists), keep_temp=args.keep_temp,
            validate_bundle=not args.no_validate,
        )
    except DestinationExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except AtomicWriteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.manifest:
        Path(args.manifest).write_text(render_manifest(result.manifest), encoding="utf-8")
    for path in written:
        print(f"wrote {path}")
    for name in result.not_produced:
        entry = result.manifest["datasets"][name]
        print(f"not produced [{name}]: {entry.get('reason', 'unavailable')}")
    if not args.no_validate:
        for name in result.reports:
            print(f"lint [{name}]: lint OK")

    if mode is Mode.SYNTHETIC and result.assumptions_present:
        print(
            "WARNING: synthetic mode — datasets marked PRODUCED_SYNTHETIC contain ASSUMED "
            "values and are NOT fully FOCUS-conformant. See the manifest.",
            file=sys.stderr,
        )
        return _apply_exit_policy(4, args.exit_policy)
    if mode is Mode.STRICT and result.not_produced:
        return _apply_exit_policy(3, args.exit_policy)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    if args.official:
        from focus_data_toolkit.official_validator import run_official_validator

        if not args.focus_version:
            print("--official requires --focus-version (e.g. 1.2.0.1)", file=sys.stderr)
            return 2
        return run_official_validator(args.file, args.focus_version)

    dataset = resolve_dataset(args.dataset.replace("-", " "))
    try:
        rows = read_source_rows(args.file, dataset=dataset)
    except MalformedRecordError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = lint_focus_1_4_structure(dataset, rows, profile=_capabilities(args))
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


def _cmd_detect(args: argparse.Namespace) -> int:
    from focus_data_toolkit.schema import detect_focus_schema

    try:
        header = _read_header(args.file)
    except MalformedRecordError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        forced_dataset = resolve_dataset(args.dataset.replace("-", " ")) if args.dataset else None
        result = detect_focus_schema(header, dataset=forced_dataset, version=args.version)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
        return 0
    print(
        f"{args.file}: dataset={result.dataset or '?'} "
        f"version={result.detected_version or '?'} "
        f"confidence={result.confidence} (score {result.score:.3f})"
    )
    if result.missing_columns:
        print(f"  missing: {', '.join(result.missing_columns)}")
    if result.unknown_columns:
        print(f"  unknown: {', '.join(result.unknown_columns)}")
    if result.extension_columns:
        print(f"  x_ extensions: {', '.join(result.extension_columns)}")
    for note in result.notes:
        print(f"  note: {note}")
    return 0


class _FileRows:
    """Re-iterable row source over a bundle input file (CSV/Parquet) for ``validate-bundle``.

    ``validate_dataset_bundle`` makes several independent forward passes, so each ``__iter__``
    opens a fresh reader — the dataset is never materialised, keeping memory bounded.
    """

    def __init__(self, path: str, dataset: str) -> None:
        self._path = path
        self._dataset = dataset

    def __iter__(self):
        from focus_data_toolkit.io.row_source import open_row_source

        with contextlib.closing(open_row_source(self._path, dataset=self._dataset)) as reader:
            for record in reader:
                yield record.values


def _detect_bundle_dir(directory: str) -> dict[str, str]:
    """Map each FOCUS dataset in ``directory`` to its file path (auto-detected from headers).

    Raises ``ValueError`` when two files look like the same dataset (ambiguous) or none are FOCUS.
    """
    from focus_data_toolkit.schema import detect_focus_schema

    base = Path(directory)
    if not base.is_dir():
        raise ValueError(f"not a directory: {directory}")
    found: dict[str, Path] = {}
    for path in sorted(base.iterdir()):
        if path.name.startswith(".") or path.name == "SHA256SUMS":
            continue
        if path.is_file() and path.suffix.lower() not in (".csv", ".gz", ".parquet"):
            continue
        # A directory is a candidate only if it looks like a Hive-partitioned Parquet dataset
        # (COL=value subdirectories) — which open_row_source reads natively. Without this, a
        # partitioned Cost and Usage dataset would be skipped and its cross-dataset checks lost.
        if path.is_dir() and not _looks_partitioned(path):
            continue
        try:
            header = _read_header(str(path))
        except (MalformedRecordError, OSError):
            continue
        result = detect_focus_schema(header)
        if result.dataset is None or result.confidence == "LOW":
            continue
        if result.detected_version != "1.4":
            continue  # validate-bundle validates a FOCUS 1.4 bundle; skip 1.2/1.3 exports
        if result.dataset in found:
            raise ValueError(
                f"ambiguous bundle: {found[result.dataset].name} and {path.name} both look "
                f"like {result.dataset}; use the per-dataset file flags instead"
            )
        found[result.dataset] = path
    if not found:
        raise ValueError(f"no FOCUS 1.4 datasets detected under {directory}")
    return {name: str(path) for name, path in found.items()}


def _looks_partitioned(directory: Path) -> bool:
    """Whether ``directory`` has ``COL=value`` subdirectories (a Hive-partitioned dataset root)."""
    try:
        return any(child.is_dir() and "=" in child.name for child in directory.iterdir())
    except OSError:
        return False


def _cmd_validate_bundle(args: argparse.Namespace) -> int:
    import tempfile

    from focus_data_toolkit.storage.spill import SpillableIndexPool
    from focus_data_toolkit.validate.bundle import validate_dataset_bundle

    explicit = {
        "Cost and Usage": args.cost_and_usage,
        "Contract Commitment": args.contract_commitment,
        "Billing Period": args.billing_period,
        "Invoice Detail": args.invoice_detail,
    }
    given = {name: path for name, path in explicit.items() if path}
    if args.directory and given:
        print(
            "error: use either --directory or the per-dataset file flags, not both",
            file=sys.stderr,
        )
        return 2
    if not args.directory and not given:
        print(
            "error: provide --directory or at least one per-dataset file flag (e.g. "
            "--cost-and-usage FILE)",
            file=sys.stderr,
        )
        return 2

    try:
        mapping = _detect_bundle_dir(args.directory) if args.directory else given
    except (MalformedRecordError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    bundle = {name: _FileRows(path, name) for name, path in mapping.items()}
    tmpdir = tempfile.mkdtemp(prefix="fdt-bundle-")
    spill = SpillableIndexPool(Path(tmpdir) / "_bundle.sqlite")
    try:
        report = validate_dataset_bundle(bundle, index_factory=spill.make_map)
    except MalformedRecordError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        spill.close()
        shutil.rmtree(tmpdir, ignore_errors=True)

    if args.report:
        Path(args.report).write_text(
            json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        # stderr, so `--format json` keeps stdout a single parseable JSON document.
        print(f"wrote {args.report}", file=sys.stderr)
    if args.format == "json":
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(f"validated: {', '.join(sorted(mapping))}")
        print(report.format())
    return 0 if report.ok else 1


def _cmd_version(args: argparse.Namespace) -> int:
    from focus_data_toolkit import __version__

    try:
        import pyarrow  # noqa: F401

        parquet = "available"
    except ModuleNotFoundError:
        parquet = "not installed (pip install 'focus-data-toolkit[parquet]')"
    print(f"focus-data-toolkit {__version__}")
    print(f"  python {sys.version.split()[0]}")
    print(f"  parquet: {parquet}")
    return 0


def _cmd_ui(args: argparse.Namespace) -> int:
    try:
        from focus_data_toolkit import studio
    except ImportError:
        print(
            "error: the Studio web UI needs the [studio] extra: "
            "pip install 'focus-data-toolkit[studio]' (or [studio-all] for Parquet)",
            file=sys.stderr,
        )
        return 2
    try:
        max_upload = _parse_size(args.max_upload)
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return studio.run(
        host=args.host,
        port=args.port,
        root=args.root,
        work_dir=args.work_dir,
        allow_remote=args.allow_remote,
        open_browser=not args.no_open_browser,
        max_upload_bytes=max_upload or 200 * 1000 * 1000,
    )


def _cmd_clean(args: argparse.Namespace) -> int:
    from focus_data_toolkit.io.atomic_writer import clean_leftovers

    # The directory itself may be missing precisely because a crash interrupted a replace
    # mid-swap — recovery restores it from the journal — so only its parent must exist.
    target = Path(args.out)
    if not target.exists() and not target.parent.is_dir():
        print(f"error: neither {target} nor its parent directory exists", file=sys.stderr)
        return 2
    actions = clean_leftovers(target)
    for action in actions:
        print(action)
    if not actions:
        print("nothing to clean")
    return 0


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
    conv.add_argument(
        "--cost-and-usage", required=True,
        help="FOCUS 1.2/1.3 Cost and Usage source (CSV, gzip ok, or Parquet)",
    )
    conv.add_argument(
        "--contract-commitment",
        help="optional FOCUS 1.3 Contract Commitment source (CSV or Parquet, 13 columns)"
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
        "--no-validate", action="store_true",
        help="skip the built-in FOCUS 1.4 structural lint and the cross-dataset bundle "
        "validation gate (the skip is recorded in the manifest)",
    )
    conv.add_argument(
        "--supports",
        action="append",
        default=[],
        choices=sorted(KNOWN_CONDITIONS),
        metavar="CONDITION",
        help="declare a FOCUS applicability condition the source supports (repeatable); "
        "conditionally-required columns are enforced only for declared conditions "
        f"(known: {', '.join(sorted(KNOWN_CONDITIONS))})",
    )
    conv.add_argument(
        "--supplement",
        action="append",
        default=[],
        metavar="FILE[:KIND]",
        help="supplemental client facts (CSV/JSON, gzip ok); repeatable; ':KIND' forces "
        "the kind; see 'fdt gaps' and docs/supplements.md",
    )
    conv.add_argument(
        "--supplements-dir", help="directory containing a supplements.json bundle manifest"
    )
    conv.add_argument(
        "--on-exists",
        choices=[e.value for e in OnExists],
        default=OnExists.REFUSE.value,
        help="policy when the output directory already exists: refuse (default), replace "
        "(atomic swap) or version (new versioned subdirectory)",
    )
    conv.add_argument(
        "--keep-temp",
        action="store_true",
        help="keep the staging directory on error for diagnosis",
    )
    conv.add_argument(
        "--progress",
        action="store_true",
        help="print bounded-memory conversion progress (per phase) to stderr; applies to the "
        "streaming engine (--stream / --output-format parquet / --partition-by)",
    )
    conv.add_argument(
        "--exit-policy",
        choices=("detailed", "pipeline"),
        default="detailed",
        help="detailed (default): distinct exit codes (3=strict incomplete, 4=synthetic "
        "assumptions). pipeline: exit 0 for any functional completion, non-zero only on a real "
        "failure — for orchestrators (Kubernetes/Airflow/Jenkins/AWS Batch) that treat any "
        "non-zero code as failure",
    )
    conv.add_argument(
        "--output-format",
        choices=list(OUTPUT_FORMATS),
        default="csv",
        help="output format: csv (default, byte-exact) or parquet (value-exact decimal128; "
        "uses the streaming engine and requires the [parquet] extra)",
    )
    conv.add_argument(
        "--stream",
        action="store_true",
        help="use the bounded-memory streaming engine (implied by --output-format parquet); "
        "recommended for large client files",
    )
    conv.add_argument(
        "--partition-by",
        help="Parquet only: comma-separated low-cardinality String/Date-Time Cost and Usage "
        "columns to Hive-partition the dataset by (e.g. BillingCurrency,InvoiceIssuerName)",
    )
    conv.add_argument(
        "--compression",
        choices=list(COMPRESSIONS),
        default="snappy",
        help="Parquet compression codec (default: snappy)",
    )
    conv.add_argument(
        "--target-file-size",
        help="Parquet only: approximate max part-file size per partition (e.g. 128MB); rolls to "
        "a new part file once exceeded",
    )
    conv.set_defaults(func=_cmd_convert)

    gaps = sub.add_parser(
        "gaps",
        help="report exactly which facts a client must supply to produce the four "
        "FOCUS 1.4 datasets factually from this source",
    )
    gaps.add_argument(
        "--cost-and-usage", required=True,
        help="FOCUS 1.2/1.3 Cost and Usage source (CSV, gzip ok, or Parquet)",
    )
    gaps.add_argument(
        "--contract-commitment", help="optional FOCUS 1.3 Contract Commitment CSV (13 columns)"
    )
    gaps.add_argument(
        "--source-version", help="force the FOCUS source version (1.2 or 1.3)"
    )
    gaps.add_argument(
        "--source-dataset", help="force the source dataset instead of auto-detecting it"
    )
    gaps.add_argument("--format", choices=("text", "json"), default="text")
    gaps.add_argument("--out", help="write the report to this path instead of stdout")
    gaps.set_defaults(func=_cmd_gaps)

    supp = sub.add_parser(
        "supplements", help="work with supplemental client data (see docs/supplements.md)"
    )
    supp_sub = supp.add_subparsers(dest="supplements_command", required=True)
    supp_val = supp_sub.add_parser(
        "validate", help="pre-flight check supplement files against a source"
    )
    supp_val.add_argument(
        "--cost-and-usage", required=True,
        help="FOCUS 1.2/1.3 Cost and Usage source (CSV, gzip ok, or Parquet)",
    )
    supp_val.add_argument(
        "--contract-commitment", help="optional FOCUS 1.3 Contract Commitment CSV"
    )
    supp_val.add_argument(
        "--supplement",
        action="append",
        default=[],
        metavar="FILE[:KIND]",
        help="supplement file (CSV/JSON, gzip ok); repeatable; ':KIND' forces the kind",
    )
    supp_val.add_argument(
        "--supplements-dir", help="directory containing a supplements.json bundle manifest"
    )
    supp_val.set_defaults(func=_cmd_supplements_validate)
    supp_adapters = supp_sub.add_parser(
        "adapters", help="list the provider-native export adapters (AWS/Azure/GCP)"
    )
    supp_adapters.set_defaults(func=_cmd_supplements_adapters)

    val = sub.add_parser("validate", help="validate a produced CSV/Parquet file")
    val.add_argument("file", help="CSV or Parquet file to validate")
    val.add_argument(
        "--dataset",
        default="Cost and Usage",
        help="FOCUS 1.4 dataset name for the built-in validator "
        "(cost-and-usage, contract-commitment, billing-period, invoice-detail)",
    )
    val.add_argument(
        "--supports",
        action="append",
        default=[],
        choices=sorted(KNOWN_CONDITIONS),
        metavar="CONDITION",
        help="declare a FOCUS applicability condition the source supports (repeatable)",
    )
    val.add_argument(
        "--official", action="store_true", help="run the official FinOps focus-validator instead"
    )
    val.add_argument(
        "--focus-version", help="rule-model version for --official (e.g. 1.2.0.1)"
    )
    val.set_defaults(func=_cmd_validate)

    det = sub.add_parser(
        "detect", help="detect the FOCUS dataset/version of a file's header"
    )
    det.add_argument("file", help="CSV (gzip ok) or Parquet file")
    det.add_argument("--dataset", help="force the dataset instead of auto-detecting it")
    det.add_argument("--version", help="force the FOCUS version (1.2/1.3/1.4)")
    det.add_argument("--format", choices=("text", "json"), default="text")
    det.set_defaults(func=_cmd_detect)

    vb = sub.add_parser(
        "validate-bundle",
        help="cross-dataset validation of a FOCUS 1.4 bundle (referential integrity, "
        "reconciliation, allocation, corrections, lifecycle)",
    )
    vb.add_argument(
        "--directory",
        help="directory of FOCUS 1.4 dataset files; datasets are auto-detected from headers "
        "(mutually exclusive with the per-dataset flags below)",
    )
    vb.add_argument("--cost-and-usage", help="Cost and Usage file (CSV, gzip ok, or Parquet)")
    vb.add_argument("--contract-commitment", help="Contract Commitment file")
    vb.add_argument("--billing-period", help="Billing Period file")
    vb.add_argument("--invoice-detail", help="Invoice Detail file")
    vb.add_argument("--report", help="write the bundle report JSON to this path")
    vb.add_argument("--format", choices=("text", "json"), default="text")
    vb.set_defaults(func=_cmd_validate_bundle)

    ver = sub.add_parser(
        "version", help="print the toolkit version and optional-extra availability"
    )
    ver.set_defaults(func=_cmd_version)

    ui = sub.add_parser(
        "ui",
        help="launch the local Studio web UI (needs the [studio] extra); localhost-only by default",
    )
    ui.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    ui.add_argument("--port", type=int, default=8765, help="port (default: 8765)")
    ui.add_argument(
        "--root", default=".", help="directory the UI may read source files from (default: .)"
    )
    ui.add_argument("--work-dir", help="scratch/output directory (default: a fresh temp dir)")
    ui.add_argument(
        "--allow-remote",
        action="store_true",
        help="permit binding a non-loopback --host (the access token is still required); "
        "expose only on trusted networks",
    )
    ui.add_argument(
        "--no-open-browser", action="store_true", help="do not open a browser on start"
    )
    ui.add_argument(
        "--max-upload", default="200MB", help="max browser upload size (default: 200MB)"
    )
    ui.set_defaults(func=_cmd_ui)

    clean = sub.add_parser(
        "clean",
        help="recover interrupted publishes and remove leftover staging/trash directories",
    )
    clean.add_argument(
        "--out", required=True,
        help="directory to clean (an output directory or the directory containing outputs); "
        "run only when no conversion is publishing there",
    )
    clean.set_defaults(func=_cmd_clean)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
