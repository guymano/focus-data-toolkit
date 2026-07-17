"""Deterministic generator for synthetic AWS data in the FOCUS 1.3 format.

Thin shim: all logic lives in :mod:`focus_data_toolkit.generators.engine`, bound to the AWS
provider profile and the FOCUS 1.3 version adapter. Exposes the historical module API
(``COLUMNS``, ``CONTRACT_COMMITMENT_COLUMNS``, ``generate_rows``, ``generate_csv_bytes``,
``generate_contract_commitment_csv_bytes``, ``main``) and the
``python -m focus_data_toolkit.generators.generate_aws_focus_1_3`` entry point unchanged.
"""

from __future__ import annotations

from focus_data_toolkit.generators._shim import build_module_api
from focus_data_toolkit.generators.providers.aws import AWS
from focus_data_toolkit.generators.versions.v1_3 import V13

_api = build_module_api(AWS, V13)
globals().update(_api)

if __name__ == "__main__":
    raise SystemExit(_api["main"]())
