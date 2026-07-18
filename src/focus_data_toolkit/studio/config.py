"""Studio runtime configuration (bind address, allowlisted root, limits, per-start token)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from focus_data_toolkit.studio.security import new_token

# Defaults chosen for a local single-user tool.
DEFAULT_PORT = 8765
DEFAULT_MAX_UPLOAD_BYTES = 200 * 1000 * 1000  # 200 MB — uploads are the *secondary* path
DEFAULT_MAX_GENERATE_ROWS = 100_000  # generation is eager (in-memory); cap it in the UI
DEFAULT_PREVIEW_LIMIT = 50
MAX_PREVIEW_LIMIT = 500
DEFAULT_JOB_TTL_SECONDS = 24 * 3600


@dataclass
class StudioConfig:
    """Resolved configuration for a Studio server instance."""

    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    root: Path = field(default_factory=Path.cwd)
    work_dir: Path | None = None  # scratch/output root; defaults to a temp dir under the system temp
    allow_remote: bool = False
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    max_generate_rows: int = DEFAULT_MAX_GENERATE_ROWS
    job_ttl_seconds: int = DEFAULT_JOB_TTL_SECONDS
    max_concurrency: int = 1  # one conversion at a time by default
    token: str = field(default_factory=new_token)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        if self.work_dir is not None:
            self.work_dir = Path(self.work_dir).resolve()

    def url(self) -> str:
        """The URL a user opens — includes the token so only the launcher can drive the API."""
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::", "") else self.host
        return f"http://{host}:{self.port}/?token={self.token}"
