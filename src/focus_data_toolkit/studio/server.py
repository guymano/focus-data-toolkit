"""Launch the Studio web server (uvicorn), loopback-only unless explicitly allowed."""

from __future__ import annotations

import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

from focus_data_toolkit.studio.app import create_app
from focus_data_toolkit.studio.config import (
    DEFAULT_MAX_GENERATE_ROWS,
    DEFAULT_MAX_UPLOAD_BYTES,
    DEFAULT_PORT,
    StudioConfig,
)
from focus_data_toolkit.studio.security import is_loopback_host


def run(
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    root: str | Path | None = None,
    work_dir: str | Path | None = None,
    allow_remote: bool = False,
    open_browser: bool = True,
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    max_generate_rows: int = DEFAULT_MAX_GENERATE_ROWS,
) -> int:
    """Start the Studio. Refuses a non-loopback bind unless ``allow_remote`` is set."""
    if not is_loopback_host(host) and not allow_remote:
        print(
            f"error: refusing to bind non-loopback host {host!r} without --allow-remote "
            "(the Studio serves local files; expose it only on trusted networks)",
            file=sys.stderr,
        )
        return 2
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="fdt-studio-"))
    config = StudioConfig(
        host=host,
        port=port,
        root=Path(root) if root else Path.cwd(),
        work_dir=Path(work_dir),
        allow_remote=allow_remote,
        max_upload_bytes=max_upload_bytes,
        max_generate_rows=max_generate_rows,
    )
    app = create_app(config)
    url = config.url()
    print(f"Focus Data Toolkit Studio: {url}")
    print(f"  root: {config.root}")
    print(f"  work: {config.work_dir}")
    if not is_loopback_host(host):
        print("  WARNING: bound to a non-loopback address — the access token is the only guard.")
    if open_browser:
        threading.Timer(1.0, lambda: _open(url)).start()

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def _open(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # a headless environment has no browser — never fatal
        pass
