"""focus-data-toolkit — FOCUS sample-data generation, 1.x -> 1.4 conversion, validation.

Public API:

- :func:`focus_data_toolkit.convert.convert_to_focus_1_4` — convert FOCUS 1.2/1.3
  rows into the four FOCUS 1.4 datasets.
- :func:`focus_data_toolkit.model.validator.validate_focus_1_4` — validate rows
  against the committed FOCUS 1.4 data model.
- :mod:`focus_data_toolkit.generators` — deterministic, provider-realistic
  FOCUS 1.2/1.3 source generators for AWS, Azure and GCP.
"""

from focus_data_toolkit.convert import ConversionResult, convert_to_focus_1_4
from focus_data_toolkit.model.validator import (
    ValidationReport,
    Violation,
    validate_focus_1_4,
)

__version__ = "0.1.0"

__all__ = [
    "ConversionResult",
    "ValidationReport",
    "Violation",
    "__version__",
    "convert_to_focus_1_4",
    "validate_focus_1_4",
]
