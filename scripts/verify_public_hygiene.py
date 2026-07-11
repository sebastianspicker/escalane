#!/usr/bin/env python3
"""Reject private or generated files from the public repository candidate.

The candidate includes tracked files and non-ignored untracked files. Ignored
local workspaces are deliberately outside this check and remain on the machine.
"""

from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path, PurePosixPath

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PREFIXES = (
    ".agents/",
    ".claude/",
    ".codegraph/",
    ".codex/",
    ".github/codex/",
    ".impeccable/",
    ".kilo/",
    ".serena/",
    "archive/",
    "backups/",
    "docs/agent/",
    "docs/archive/",
    "docs/source-audit/",
    "external/",
    "exports/",
    "private/",
    "privat/",
    "reports/",
    "scratch/",
    "third-party/",
    "third_party/",
    "tmp/",
    "vendor/",
)
FORBIDDEN_DIRECTORY_NAMES = frozenset(
    {
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".pyre",
        ".pytype",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "blob-report",
        "build",
        "coverage",
        "dist",
        "htmlcov",
        ".ipynb_checkpoints",
        "node_modules",
        "playwright-report",
        "test-results",
        "venv",
    }
)
FORBIDDEN_BASENAMES = frozenset(
    {
        ".DS_Store",
        ".coverage",
        ".envrc",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "AGENTS.md",
        "AGENT_AUDIT_REPORT.md",
        "AUDIT.md",
        "Desktop.ini",
        "HARNESS_PRINCIPLES.md",
        "code_review.md",
        "coverage.xml",
        "codex-output.md",
        "codex-status.txt",
        "credentials.json",
        "docker-compose.override.yaml",
        "docker-compose.override.yml",
        "progress.md",
        "trace.zip",
        "Thumbs.db",
    }
)
FORBIDDEN_NAME_PATTERNS = (
    "client_secret*.json",
    "service-account*.json",
)
FORBIDDEN_SUFFIXES = frozenset(
    {
        ".cer",
        ".cover",
        ".crt",
        ".db",
        ".der",
        ".dump",
        ".har",
        ".jks",
        ".kdbx",
        ".key",
        ".keystore",
        ".log",
        ".p12",
        ".patch",
        ".pem",
        ".pfx",
        ".pyc",
        ".pyo",
        ".rdb",
        ".sarif",
        ".sqlite",
        ".sqlite3",
        ".tmp",
        ".bak",
        ".webm",
        ".aof",
    }
)
PRIVATE_TEXT_MARKERS = (
    b"-----BEGIN " + b"PRIVATE KEY-----",
    b"-----BEGIN OPENSSH " + b"PRIVATE KEY-----",
    b"-----BEGIN RSA " + b"PRIVATE KEY-----",
    b"/" + b"Users/",
    b"C:" + b"\\Users\\",
)


def _candidate_paths(arguments: list[str]) -> list[str]:
    if arguments == ["--null"]:
        arguments = [
            path for path in sys.stdin.buffer.read().decode().split("\0") if path
        ]
    return sorted(
        path
        for path in arguments
        if (REPOSITORY_ROOT / path).exists() or (REPOSITORY_ROOT / path).is_symlink()
    )


def _prefix_reason(relative_path: str) -> str | None:
    if relative_path.startswith(FORBIDDEN_PREFIXES):
        return "local-only workspace or report path"
    return None


def _directory_reason(path: PurePosixPath) -> str | None:
    if FORBIDDEN_DIRECTORY_NAMES.intersection(path.parts[:-1]):
        return "generated dependency, cache, report, or build directory"
    return None


def _basename_reason(basename: str) -> str | None:
    if basename in FORBIDDEN_BASENAMES:
        return "local-only or credential filename"
    if basename.startswith(".env") and basename != ".env.example":
        return "environment file"
    if basename.startswith("._"):
        return "operating-system metadata"
    if basename in {"id_rsa", "id_ed25519"}:
        return "private key filename"
    if any(fnmatch.fnmatch(basename, pattern) for pattern in FORBIDDEN_NAME_PATTERNS):
        return "credential filename"
    return None


def _suffix_reason(relative_path: str, path: PurePosixPath) -> str | None:
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return "sensitive or generated file extension"
    if (
        relative_path.startswith("docs/assets/screenshots/")
        and path.suffix.lower() == ".png"
    ):
        return "regenerated screenshot asset"
    return None


def _path_reason(relative_path: str) -> str | None:
    path = PurePosixPath(relative_path)
    return (
        _prefix_reason(relative_path)
        or _directory_reason(path)
        or _basename_reason(path.name)
        or _suffix_reason(relative_path, path)
    )


def _content_reason(path: Path) -> str | None:
    if path.is_symlink():
        target = os.readlink(path)
        resolved_target = (path.parent / target).resolve(strict=False)
        if not resolved_target.is_relative_to(REPOSITORY_ROOT):
            return "symlink points outside the repository"
        return None

    data = path.read_bytes()
    if b"\0" in data:
        return None
    if any(marker in data for marker in PRIVATE_TEXT_MARKERS):
        return "private key material or absolute user-home path"
    return None


def main(arguments: list[str] | None = None) -> int:
    violations: list[tuple[str, str]] = []
    candidates = _candidate_paths(sys.argv[1:] if arguments is None else arguments)

    for relative_path in candidates:
        reason = _path_reason(relative_path)
        if reason is None:
            reason = _content_reason(REPOSITORY_ROOT / relative_path)
        if reason is not None:
            violations.append((relative_path, reason))

    if violations:
        print("Public repository hygiene check failed:", file=sys.stderr)
        for relative_path, reason in violations:
            print(f"- {relative_path}: {reason}", file=sys.stderr)
        return 1

    print(
        f"Public repository hygiene check passed ({len(candidates)} candidate files)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
