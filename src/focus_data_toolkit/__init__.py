"""focus-data-toolkit — FOCUS sample-data generation, 1.x -> 1.4 conversion, validation.

Public API:

- :func:`focus_data_toolkit.convert.convert_to_focus_1_4` — convert FOCUS 1.2/1.3
  rows into the four FOCUS 1.4 datasets.
- :func:`focus_data_toolkit.model.validator.lint_focus_1_4_structure` — structurally
  + semantically lint rows against the committed FOCUS 1.4 data model. (This is a
  linter, not a full FOCUS conformance validator; ``validate_focus_1_4`` is a
  deprecated alias.)
- :mod:`focus_data_toolkit.generators` — deterministic, provider-realistic
  FOCUS 1.2/1.3 source generators for AWS, Azure and GCP.
"""

from focus_data_toolkit.convert import ConversionResult, convert_to_focus_1_4
from focus_data_toolkit.model.validator import (
    LintReport,
    ValidationReport,
    Violation,
    lint_focus_1_4_structure,
    validate_focus_1_4,
)

__version__ = "0.2.0"

__all__ = [
    "ConversionResult",
    "LintReport",
    "ValidationReport",
    "Violation",
    "__version__",
    "convert_to_focus_1_4",
    "lint_focus_1_4_structure",
    "validate_focus_1_4",
]
