"""FOCUS 1.4 data model (committed JSON artifact) and helpers.

The model is derived from the FinOps Foundation "FOCUS 1.4 Data Model"
workbook (https://focus.finops.org) by ``tools/extract_focus_1_4_model.py``;
the JSON is the artifact of record for this package.
"""

from __future__ import annotations

from focus_data_toolkit.model.validator import load_model, resolve_dataset

FOCUS_1_4_DATASETS: tuple[str, ...] = (
    "Cost and Usage",
    "Contract Commitment",
    "Billing Period",
    "Invoice Detail",
)


def dataset_columns(dataset: str) -> tuple[str, ...]:
    """Return the FOCUS 1.4 column names of ``dataset``, in model order."""
    return tuple(load_model()["datasets"][resolve_dataset(dataset)]["columns"])


def column_spec(dataset: str, column: str) -> dict:
    """Return the model spec of one column (feature_level, allows_nulls, ...)."""
    return load_model()["datasets"][resolve_dataset(dataset)]["columns"][column]


__all__ = ["FOCUS_1_4_DATASETS", "column_spec", "dataset_columns", "load_model", "resolve_dataset"]
