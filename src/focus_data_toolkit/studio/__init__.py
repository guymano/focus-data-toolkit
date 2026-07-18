"""Focus Data Toolkit Studio — a LOCAL web UI over the same Core.

The Studio never reimplements FOCUS logic: it drives the exact SDK the CLI and Runner use
(``detect_focus_schema``, ``convert_files``, ``validate_dataset_bundle``, the generators), so its
manifests, diagnostics and checksums are identical to a CLI run. It is designed for **local,
single-user** use: it binds to loopback by default, requires a per-start token, validates
Host/Origin headers, confines file access to an allowlisted root, and processes on the bounded
streaming path — data never leaves the machine.

This subpackage lives behind the optional ``[studio]`` extra (FastAPI + uvicorn). The CLI's
``focus-toolkit ui`` command imports it lazily, so a core install without the extra still works.
"""

from __future__ import annotations

from focus_data_toolkit.studio.config import StudioConfig
from focus_data_toolkit.studio.server import run

__all__ = ["StudioConfig", "run"]
