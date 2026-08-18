"""Reject private or generated files from the public repository candidate.

The candidate includes tracked files and non-ignored untracked files. Ignored
local workspaces are deliberately outside this check and remain on the machine.
"""

from __future__ import annotations

import fnmatch
import os
import re
import struct
import subprocess
import sys
from pathlib import Path, PurePosixPath

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PREFIXES = (
    ".agents/",
    ".claude/",
    ".codegraph/",
    ".codex/",
    ".continue/",
    ".cursor/",
    ".github/codex/",
    ".github/instructions/",
    ".github/prompts/",
    ".impeccable/",
    ".kilo/",
    ".serena/",
    ".windsurf/",
    "archive/",
    "backups/",
    "conversation-exports/",
    "docs/agent/",
    "docs/archive/",
    "docs/source-audit/",
    "external/",
    "exports/",
    "private/",
    "privat/",
    "reports/",
    "scratch/",
    "screenshot-review/",
    "prompts/",
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
        "CLAUDE.md",
        "Desktop.ini",
        "GEMINI.md",
        "HARNESS_PRINCIPLES.md",
        "PROMPTS.md",
        "code_review.md",
        "copilot-instructions.md",
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
PRIVATE_TOKEN_PATTERNS = (
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{36,255}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{80,255}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"AIza[0-9A-Za-z_-]{35}"),
    re.compile(rb"xox[baprs]-[0-9A-Za-z-]{20,}"),
    re.compile(rb"sk-[A-Za-z0-9_-]{32,}"),
)
LEGACY_BRAND_MARKERS = (
    b"Alarm " + b"Broker",
    b"Alarm" + b"Broker",
    b"alarm-" + b"broker",
    b"alarm_" + b"broker",
    b"ALARM_" + b"BROKER",
)
LEGACY_BRAND_ALLOWED_PATHS = frozenset({"CHANGELOG.md", "docs/SETUP.md"})

CURATED_SCREENSHOTS = {
    "docs/assets/screenshots/01-admin-overview.png": (1440, 720),
    "docs/assets/screenshots/04-admin-alarm-detail.png": (1440, 720),
    "docs/assets/screenshots/06-ack-page-triggered-mobile.png": (390, 700),
    "docs/assets/screenshots/09-simulation-feed.png": (1440, 720),
}


def _git_paths(arguments: list[str]) -> list[str]:
    """Return NUL-delimited repository paths from one read-only Git query."""
    result = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=False,
    )
    return [path for path in result.stdout.decode().split("\0") if path]


def _deleted_worktree_paths() -> set[str]:
    """List intentional unstaged deletions without treating unreadable files as deleted."""
    return set(_git_paths(["diff", "--name-only", "--diff-filter=D", "-z", "--", "."]))


def _worktree_paths(paths: list[str], deleted_paths: set[str]) -> list[str]:
    """Remove only Git-confirmed deletions from the candidate file list."""
    return [path for path in paths if path not in deleted_paths]


def _repository_candidates() -> list[str]:
    """List the tracked and non-ignored worktree that is eligible for publication."""
    paths = _git_paths(["ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    return _worktree_paths(paths, _deleted_worktree_paths())


def _candidate_paths(arguments: list[str]) -> list[str]:
    """Normalize explicit paths or the NUL-delimited Git worktree inventory."""
    if not arguments:
        arguments = _repository_candidates()
    elif arguments == ["--null"]:
        arguments = _worktree_paths(
            [path for path in sys.stdin.buffer.read().decode().split("\0") if path],
            _deleted_worktree_paths(),
        )
    return sorted(set(arguments))


def _prefix_reason(relative_path: str) -> str | None:
    """Reject repository areas reserved for local tooling or private artifacts."""
    if relative_path.startswith(FORBIDDEN_PREFIXES):
        return "local-only workspace or report path"
    return None


def _directory_reason(path: PurePosixPath) -> str | None:
    """Reject generated or dependency directories wherever they are nested."""
    if FORBIDDEN_DIRECTORY_NAMES.intersection(path.parts[:-1]):
        return "generated dependency, cache, report, or build directory"
    return None


def _basename_reason(basename: str) -> str | None:
    """Reject filenames commonly associated with credentials or local state."""
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
    """Reject sensitive extensions and screenshots outside the curated allowlist."""
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return "sensitive or generated file extension"
    if (
        relative_path.startswith("docs/assets/screenshots/")
        and path.suffix.lower() == ".png"
        and relative_path not in CURATED_SCREENSHOTS
    ):
        return "unreviewed screenshot asset"
    return None


def _path_reason(relative_path: str) -> str | None:
    """Return the first structural policy violation for a candidate path."""
    path = PurePosixPath(relative_path)
    if path.is_absolute() or ".." in path.parts:
        return "candidate path escapes the repository"
    return (
        _prefix_reason(relative_path)
        or _directory_reason(path)
        or _basename_reason(path.name)
        or _suffix_reason(relative_path, path)
    )


def _symlink_reason(path: Path) -> str | None:
    """Reject a symlink whose target escapes the repository."""
    target = os.readlink(path)
    resolved_target = (path.parent / target).resolve(strict=False)
    if not resolved_target.is_relative_to(REPOSITORY_ROOT):
        return "symlink points outside the repository"
    return None


def _text_content_reason(data: bytes, relative_path: str) -> str | None:
    """Return the first private marker found in text-like candidate content."""
    if any(marker in data for marker in PRIVATE_TEXT_MARKERS):
        return "private key material or absolute user-home path"
    if any(pattern.search(data) for pattern in PRIVATE_TOKEN_PATTERNS):
        return "credential-like token material"
    if relative_path not in LEGACY_BRAND_ALLOWED_PATHS and any(
        marker in data for marker in LEGACY_BRAND_MARKERS
    ):
        return "legacy product identifier outside the migration allowlist"
    return None


def _content_reason(path: Path, relative_path: str = "") -> str | None:
    """Inspect readable content and symlinks for private or machine-specific data."""
    if path.is_symlink():
        return _symlink_reason(path)

    data = path.read_bytes()
    if b"\0" in data:
        return None
    return _text_content_reason(data, relative_path)


def _curated_screenshot_reason(path: Path, relative_path: str) -> str | None:
    """Validate PNG identity and reviewed viewport dimensions for public captures."""
    reviewed_size = CURATED_SCREENSHOTS.get(relative_path)
    if reviewed_size is None:
        return None
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return "curated screenshot is not a valid PNG"
    width, height = struct.unpack(">II", data[16:24])
    viewport_width, minimum_height = reviewed_size
    if width != viewport_width or height < minimum_height:
        return "curated screenshot does not match the reviewed viewport dimensions"
    return None


def _inspection_reason(path: Path, relative_path: str = "") -> str | None:
    """Inspect one listed candidate and fail closed if it cannot be read."""
    try:
        if not path.exists() and not path.is_symlink():
            return "candidate path is missing or inaccessible"
        if path.is_symlink():
            return _content_reason(path)
        return _curated_screenshot_reason(path, relative_path) or _content_reason(
            path, relative_path
        )
    except OSError:
        return "candidate path cannot be inspected"


def main(arguments: list[str] | None = None) -> int:
    """Inspect the public candidate and report every policy violation together."""
    violations: list[tuple[str, str]] = []
    candidates = _candidate_paths(sys.argv[1:] if arguments is None else arguments)

    for relative_path in candidates:
        reason = _path_reason(relative_path)
        if reason is None:
            reason = _inspection_reason(REPOSITORY_ROOT / relative_path, relative_path)
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
