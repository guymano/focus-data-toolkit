"""Provider profiles: only what is genuinely specific to AWS, Azure and GCP.

A provider is *data* (names, service/region/account tables) plus a few pure callables
(resource-id / sku-id formats, commitment terms). All generation logic lives in
``generators.engine``; adding a provider is one profile module, no engine change.
"""

from __future__ import annotations

from focus_data_toolkit.generators.providers.aws import AWS
from focus_data_toolkit.generators.providers.azure import AZURE
from focus_data_toolkit.generators.providers.gcp import GCP
from focus_data_toolkit.generators.providers.profile import (
    CommitmentModel,
    ProviderProfile,
    ServiceSpec,
)

PROFILES: dict[str, ProviderProfile] = {AWS.key: AWS, AZURE.key: AZURE, GCP.key: GCP}

__all__ = [
    "AWS",
    "AZURE",
    "GCP",
    "PROFILES",
    "CommitmentModel",
    "ProviderProfile",
    "ServiceSpec",
]
