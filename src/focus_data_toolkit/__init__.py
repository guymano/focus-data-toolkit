"""focus-data-toolkit — FOCUS sample-data generation, 1.x -> 1.4 conversion, validation.

Public API:

- :func:`focus_data_toolkit.convert.convert_to_focus_1_4` — convert FOCUS 1.2/1.3
  rows into the four FOCUS 1.4 datasets (small-volume, in-memory).
- :func:`focus_data_toolkit.schema.detect_focus_schema` — identify the FOCUS dataset and
  version of a header row, with a confidence assessment.
- :func:`validate_dataset_bundle` (alias :func:`validate_bundle`) — cross-dataset
  (referential / reconciliation / split-allocation / lifecycle) validation of a bundle of
  datasets. Distinct from the per-dataset linter below.
- :func:`focus_data_toolkit.model.validator.lint_focus_1_4_structure` — structurally
  + semantically lint rows against the committed FOCUS 1.4 data model. (A linter, not a full
  FOCUS conformance validator; ``validate_focus_1_4`` is a deprecated alias.)
- :mod:`focus_data_toolkit.generators` — deterministic, provider-realistic FOCUS 1.2/1.3
  source generators for AWS, Azure and GCP.
"""

from focus_data_toolkit.convert import ConversionResult, convert_to_focus_1_4
from focus_data_toolkit.model.validator import (
    LintReport,
    ValidationReport,
    Violation,
    lint_focus_1_4_structure,
    validate_focus_1_4,
)
from focus_data_toolkit.schema import SchemaDetectionResult, detect_focus_schema
from focus_data_toolkit.validate import BundleReport, validate_dataset_bundle

# Alias matching the API name used in the P1 plan / docs.
validate_bundle = validate_dataset_bundle

__version__ = "0.3.0"

__all__ = [
    "BundleReport",
    "ConversionResult",
    "LintReport",
    "SchemaDetectionResult",
    "ValidationReport",
    "Violation",
    "__version__",
    "convert_to_focus_1_4",
    "detect_focus_schema",
    "lint_focus_1_4_structure",
    "validate_bundle",
    "validate_dataset_bundle",
    "validate_focus_1_4",
]
