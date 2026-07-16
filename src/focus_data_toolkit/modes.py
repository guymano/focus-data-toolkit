"""Conversion modes.

* ``STRICT`` (default) — never invents financial facts. A canonical FOCUS 1.4 dataset
  is produced only when every Mandatory non-nullable column can be filled from factual
  lineage (observed / renamed / derived / enriched). Datasets that would require assumed
  provider-issued values are reported ``NOT_PRODUCED`` in the manifest, not fabricated.
* ``SYNTHETIC`` — for demos / tests / learning. Assumed values may be generated; the
  result is explicitly labelled synthetic and is never presented as fully conformant.
"""

from __future__ import annotations

from enum import StrEnum


class Mode(StrEnum):
    STRICT = "strict"
    SYNTHETIC = "synthetic"
