"""focus-data-toolkit — FOCUS sample-data generation, 1.x -> 1.4 conversion, validation.

Public API:

- :func:`focus_data_toolkit.convert.convert_to_focus_1_4` — convert FOCUS 1.2/1.3
  rows into the four FOCUS 1.4 datasets (small-volume, in-memory).
- :func:`focus_data_toolkit.convert.convert_files` — the same conversion, streamed from a
  Cost and Usage file with **bounded memory** (large client data), to CSV or Parquet.
- :func:`focus_data_toolkit.schema.detect_focus_schema` — identify the FOCUS dataset and
  version of a header row, with a confidence assessment.
- :func:`validate_dataset_bundle` (alias :func:`validate_bundle`) — cross-dataset
  (referential / reconciliation / split-allocation / lifecycle) validation of a bundle of
  datasets. Distinct from the per-dataset linter below.
- :func:`focus_data_toolkit.model.validator.lint_focus_1_4_structure` — structurally
  + semantically lint rows against the committed FOCUS 1.4 data model. (A linter, not a full
  FOCUS conformance validator; ``validate_focus_1_4`` is a deprecated alias.)
- :mod:`focus_data_toolkit.generators` — deterministic, provider-realistic FOCUS 1.2/1.3
  source generators for AWS, Azure and GCP. Their ``contract_commitment_rows_for`` helper
  is internal (not re-exported here; see docs/versioning.md).
- :class:`SupplementBundle` / :class:`SupplementFileSpec` / :func:`load_bundle_dir` — load
  the client supplements (FOCUS-named files or provider-native exports) passed to either
  conversion as ``supplements=``; :func:`compute_gaps` reports which columns still block
  strict production and which supplement kind satisfies each.
- :class:`Mode`, :class:`ConversionError`, :class:`SupplementError` and
  :class:`AtomicWriteError` — the conversion mode and the failures a library caller handles
  (invalid input, unusable supplement, publication refused by the validation gate).
"""

from focus_data_toolkit._version import __version__
from focus_data_toolkit.convert import (
    ConversionCancelled,
    ConversionError,
    ConversionResult,
    convert_files,
    convert_to_focus_1_4,
)
from focus_data_toolkit.io.atomic_writer import AtomicWriteError
from focus_data_toolkit.lifecycle import (
    DatasetInstance,
    check_dataset_instances,
    check_instance_chains,
    check_status_transitions,
)
from focus_data_toolkit.model.validator import (
    LintReport,
    ValidationReport,
    Violation,
    lint_focus_1_4_structure,
    validate_focus_1_4,
)
from focus_data_toolkit.modes import Mode
from focus_data_toolkit.progress import ProgressEvent
from focus_data_toolkit.schema import SchemaDetectionResult, detect_focus_schema
from focus_data_toolkit.supplement import (
    GapReport,
    SupplementBundle,
    SupplementError,
    SupplementFileSpec,
    compute_gaps,
    load_bundle_dir,
)
from focus_data_toolkit.validate import BundleReport, validate_dataset_bundle

# Alias matching the API name used in the P1 plan / docs.
validate_bundle = validate_dataset_bundle

__all__ = [
    "AtomicWriteError",
    "BundleReport",
    "ConversionCancelled",
    "ConversionError",
    "ConversionResult",
    "DatasetInstance",
    "GapReport",
    "LintReport",
    "Mode",
    "ProgressEvent",
    "SchemaDetectionResult",
    "SupplementBundle",
    "SupplementError",
    "SupplementFileSpec",
    "ValidationReport",
    "Violation",
    "__version__",
    "check_dataset_instances",
    "check_instance_chains",
    "check_status_transitions",
    "compute_gaps",
    "convert_files",
    "convert_to_focus_1_4",
    "detect_focus_schema",
    "lint_focus_1_4_structure",
    "load_bundle_dir",
    "validate_bundle",
    "validate_dataset_bundle",
    "validate_focus_1_4",
]
