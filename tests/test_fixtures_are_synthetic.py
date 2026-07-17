"""Guard: committed test fixtures are synthetic — no real client data, credentials or PII.

This project must never ship real cloud-billing data. This scan (a) rejects high-confidence
secret/PII patterns in fixture files, and (b) requires every fixture directory that holds data to
document its provenance (a SOURCES.md or README.md), so a new fixture set cannot be added without
stating that it is synthetic or spec-derived.

It complements the repo-wide gitleaks scan (which covers git history) by focusing on the fixture
*data* and its provenance. Patterns are deliberately high-confidence to avoid false positives on
legitimately synthetic identifiers (e.g. fake 12-digit account numbers).
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _ROOT / "tests" / "fixtures"

_DATA_SUFFIXES = {".csv", ".json", ".jsonl", ".tsv", ".ndjson", ".txt"}
_PROVENANCE_DOCS = {"SOURCES.md", "README.md"}

# High-confidence markers of real credentials / PII. Each must be specific enough that synthetic
# fixture data does not trip it.
_SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "private-key-block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP |DSA )?PRIVATE KEY-----"),
    "aws-access-key-id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "github-token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{36}\b|\bgithub_pat_[0-9A-Za-z_]{60,}"),
    "slack-token": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}"),
    "bearer-jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "private-ssh-header": re.compile(r"ssh-rsa AAAA[0-9A-Za-z+/]{100,}"),
}

# Emails are flagged unless they use a reserved documentation/test domain (RFC 2606 + common).
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "test", "localhost", "invalid"}


def _data_files() -> list[Path]:
    if not _FIXTURES.is_dir():
        return []
    return sorted(p for p in _FIXTURES.rglob("*") if p.is_file() and p.suffix.lower() in _DATA_SUFFIXES)


def test_fixtures_directory_exists():
    # A sanity check so the scan below is not silently vacuous if fixtures move.
    assert _FIXTURES.is_dir(), f"expected fixtures at {_FIXTURES}"


def test_no_secrets_or_credentials_in_fixtures():
    findings: list[str] = []
    for path in _data_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(_ROOT)
        for name, pattern in _SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{rel}: matched secret pattern '{name}'")
    assert not findings, "possible secrets in fixtures:\n" + "\n".join(findings)


def test_no_real_emails_in_fixtures():
    findings: list[str] = []
    for path in _data_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(_ROOT)
        for match in _EMAIL.finditer(text):
            domain = match.group(1).lower()
            if domain not in _ALLOWED_EMAIL_DOMAINS:
                findings.append(f"{rel}: non-synthetic email domain '{domain}'")
    assert not findings, (
        "possible real emails in fixtures (use example.com/.org/.net for synthetic addresses):\n"
        + "\n".join(findings)
    )


def _has_provenance_doc(directory: Path) -> bool:
    """True if this dir, or any ancestor up to tests/fixtures, carries a provenance doc.

    A doc covering a subtree counts (e.g. tests/fixtures/golden/README.md documents the
    golden/compatibility_golden/ and golden/correctness_migration/ snapshot dirs beneath it).
    """
    current = directory
    while True:
        if any((current / doc).is_file() for doc in _PROVENANCE_DOCS):
            return True
        if current == _FIXTURES:
            return False
        current = current.parent


def test_every_fixture_data_dir_documents_provenance():
    findings: list[str] = []
    dirs_with_data = {p.parent for p in _data_files()}
    for directory in sorted(dirs_with_data):
        if not _has_provenance_doc(directory):
            rel = directory.relative_to(_ROOT)
            findings.append(f"{rel}: has data files but no SOURCES.md/README.md provenance doc")
    assert not findings, "fixture directories missing a synthetic-provenance doc:\n" + "\n".join(findings)
