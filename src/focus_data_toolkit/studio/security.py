"""Security helpers for the local Studio.

Even a loopback-only server is reachable by a malicious web page open in the same browser, so the
Studio defends the surface with (a) a per-start random token required on every API call,
(b) strict ``Host`` header validation (anti DNS-rebinding), (c) ``Origin`` validation on
state-changing requests (anti-CSRF), and (d) confinement of all file access to an allowlisted
root. All checks are standard-library only.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path


def new_token() -> str:
    """A fresh URL-safe token, generated once per server start."""
    return secrets.token_urlsafe(32)


def token_matches(expected: str, provided: str | None) -> bool:
    """Constant-time token comparison; ``False`` for a missing/empty token."""
    if not provided:
        return False
    return hmac.compare_digest(expected, provided)


def is_loopback_host(host: str) -> bool:
    """Whether a *bind* address is loopback (so remote exposure needs an explicit opt-in)."""
    h = (host or "").strip().lower()
    if h in ("localhost", "::1", "0:0:0:0:0:0:0:1"):
        return True
    return h.startswith("127.")


def allowed_authorities(host: str, port: int) -> set[str]:
    """The set of acceptable ``Host`` header values (``authority`` = host[:port])."""
    hosts = {"127.0.0.1", "localhost", "[::1]", "::1"}
    if host:
        hosts.add(host)
        hosts.add(f"[{host}]")
    out: set[str] = set()
    for h in hosts:
        out.add(h.lower())
        out.add(f"{h}:{port}".lower())
    return out


def host_header_allowed(host_header: str | None, host: str, port: int) -> bool:
    """Validate the ``Host`` header against the loopback/bind allowlist (anti DNS-rebinding)."""
    if not host_header:
        return False
    return host_header.strip().lower() in allowed_authorities(host, port)


def allowed_origins(host: str, port: int) -> set[str]:
    """Acceptable ``Origin`` values for state-changing requests (same-origin only)."""
    return {f"http://{authority}" for authority in allowed_authorities(host, port)}


def origin_allowed(origin: str | None, host: str, port: int) -> bool:
    """Validate an ``Origin`` header (anti-CSRF). A missing Origin on a POST is rejected."""
    if not origin:
        return False
    return origin.strip().lower() in allowed_origins(host, port)


class PathOutsideRoot(ValueError):
    """Raised when a requested path escapes the allowlisted root directory."""


def resolve_within_root(user_path: str, root: Path) -> Path:
    """Resolve ``user_path`` to an existing entry under ``root`` by walking the directory tree.

    ``root`` is always a server-controlled directory (never user input). Absolute inputs and any
    ``..`` segment are refused. Each remaining component is matched **by name** against the actual
    entries of the current directory (via :func:`os.scandir`), and resolution descends into the
    matched entry. The returned path is therefore built entirely from real directory entries — the
    user string only ever participates in an equality comparison, never in path construction — so
    it cannot point outside ``root``, and no user-controlled data reaches a filesystem operation.
    Nested browsing (``a/b/c.csv``) is fully supported; a component with no matching entry raises
    :class:`PathOutsideRoot`.
    """
    if os.path.isabs(user_path) or user_path[:1] in ("/", "\\"):
        raise PathOutsideRoot(f"absolute paths are not allowed: {user_path!r}")
    current = Path(os.path.realpath(root))
    for raw in user_path.replace("\\", "/").split("/"):
        if raw in ("", "."):
            continue
        if raw == "..":
            raise PathOutsideRoot(f"path {user_path!r} is outside the allowed root")
        match: Path | None = None
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.name == raw:
                        match = Path(entry.path)
                        break
        except OSError as exc:
            raise PathOutsideRoot(f"cannot resolve {user_path!r} under the allowed root") from exc
        if match is None:
            raise PathOutsideRoot(f"no entry {raw!r} under the allowed root")
        current = match
    return current
