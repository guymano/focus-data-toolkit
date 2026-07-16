"""FOCUS schema knowledge: per-(dataset, version) column registry and detection.

The registry (:mod:`focus_data_toolkit.schema.registry`) derives the normative column
set of each FOCUS dataset at each supported version from the committed FOCUS 1.4 model
(every column carries its introduction ``version``) plus a small table of columns removed
by 1.4. The detector (:mod:`focus_data_toolkit.schema.detection`) uses it to identify the
dataset and version of an arbitrary header row, with a confidence assessment.
"""

from __future__ import annotations

from focus_data_toolkit.schema.detection import (
    SchemaDetectionResult,
    detect_focus_schema,
)

__all__ = ["SchemaDetectionResult", "detect_focus_schema"]
