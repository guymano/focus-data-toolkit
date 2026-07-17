"""Deterministic generator for synthetic Google Cloud data in the FOCUS 1.2 format.

Thin shim: logic lives in :mod:`focus_data_toolkit.generators.engine`, bound to the GCP
provider profile and the FOCUS 1.2 version adapter.
"""

from __future__ import annotations

from focus_data_toolkit.generators._shim import build_module_api
from focus_data_toolkit.generators.providers.gcp import GCP
from focus_data_toolkit.generators.versions.v1_2 import V12

_api = build_module_api(GCP, V12)
globals().update(_api)

if __name__ == "__main__":
    raise SystemExit(_api["main"]())
