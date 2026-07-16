"""Cross-dataset validation layer (P1.4 / P1.8 / P1.9).

Distinct from the per-dataset linter in ``model/validator.py``: this layer validates a bundle
of datasets against each other.
"""

from __future__ import annotations

from focus_data_toolkit.validate.bundle import (
    Bundle,
    BundleReport,
    validate_dataset_bundle,
)

__all__ = ["Bundle", "BundleReport", "validate_dataset_bundle"]
