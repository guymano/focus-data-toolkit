"""Security helpers for the local Studio.

Even a loopback-only server is reachable by a malicious web page open in the same browser, so the
Studio defends the surface with (a) a per-start random token required on every API call,
(b) strict ``Host`` header validation (anti DNS-rebinding), (c) ``Origin`` validation on
state-changing requests (anti-CSRF), and (d) confinement of all file access to an allowlisted
root. All checks are standard-library only.
"""

from __future__ import annotations

import hmac
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
    """Resolve ``user_path`` under ``root``; reject absolute paths and any ``..``/symlink escape.

    Absolute inputs are refused outright, and the fully resolved candidate must be *contained* by
    the resolved root (``Path.is_relative_to``) — this containment check is the barrier that makes
    the returned path safe to use in a filesystem operation.
    """
    root_resolved = Path(root).resolve()
    candidate = Path(user_path)
    if candidate.is_absolute():
        raise PathOutsideRoot(f"absolute paths are not allowed: {user_path!r}")
    resolved = (root_resolved / candidate).resolve()
    if not resolved.is_relative_to(root_resolved):
        raise PathOutsideRoot(f"path {user_path!r} is outside the allowed root")
    return resolved
