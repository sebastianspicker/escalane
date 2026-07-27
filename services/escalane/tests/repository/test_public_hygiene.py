"""Fail-closed tests for the repository's public-release hygiene scanner."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.verify_public_hygiene import (  # noqa: E402
    _candidate_paths,
    _content_reason,
    _inspection_reason,
    _path_reason,
    _worktree_paths,
)

pytestmark = pytest.mark.repository


@pytest.mark.parametrize(
    ("relative_path", "expected_reason"),
    [
        (".env", "environment file"),
        (".cursor/rules/project.mdc", "local-only workspace or report path"),
        (".github/prompts/review.prompt.md", "local-only workspace or report path"),
        ("conversation-exports/session.md", "local-only workspace or report path"),
        ("docs/archive/report.md", "local-only workspace or report path"),
        ("prompts/release-review.md", "local-only workspace or report path"),
        ("private/notes.md", "local-only workspace or report path"),
        ("vendor/copied-source.py", "local-only workspace or report path"),
        ("backups/alarm.dump", "local-only workspace or report path"),
        ("screenshot-review/admin.png", "local-only workspace or report path"),
        ("service/build/output.txt", "generated dependency, cache, report, or build directory"),
        ("credentials.json", "local-only or credential filename"),
        ("CLAUDE.md", "local-only or credential filename"),
        (".github/copilot-instructions.md", "local-only or credential filename"),
        ("capture.har", "sensitive or generated file extension"),
        ("deploy/docker-compose.override.yml", "local-only or credential filename"),
        ("docs/assets/screenshots/admin.png", "unreviewed screenshot asset"),
        ("../outside.txt", "candidate path escapes the repository"),
        ("/absolute.txt", "candidate path escapes the repository"),
    ],
)
def test_public_hygiene_rejects_private_or_generated_paths(
    relative_path: str, expected_reason: str
) -> None:
    assert _path_reason(relative_path) == expected_reason


@pytest.mark.parametrize(
    "relative_path",
    [
        ".env.example",
        "PRODUCT.md",
        "docs/FRONTEND.md",
        "docs/assets/screenshots/01-admin-overview.png",
        "services/escalane/escalane/api/assets/ui.css",
    ],
)
def test_public_hygiene_accepts_public_paths(relative_path: str) -> None:
    assert _path_reason(relative_path) is None


def test_public_hygiene_rejects_machine_specific_text(tmp_path: Path) -> None:
    candidate = tmp_path / "notes.txt"
    candidate.write_text('path="/' + 'Users/example/private"\n', encoding="utf-8")

    assert _content_reason(candidate) == "private key material or absolute user-home path"


@pytest.mark.parametrize(
    "token",
    [
        "gh" + "p_" + "a" * 40,
        "AK" + "IA" + "A" * 16,
        "AI" + "za" + "A" * 35,
        "xox" + "b-" + "1" * 24,
        "s" + "k-" + "a" * 40,
    ],
)
def test_public_hygiene_rejects_high_confidence_tokens(tmp_path: Path, token: str) -> None:
    candidate = tmp_path / "public.txt"
    candidate.write_text(f"value={token}\n", encoding="utf-8")

    assert _content_reason(candidate) == "credential-like token material"


@pytest.mark.parametrize(
    "legacy_name",
    [
        "Alarm " + "Broker",
        "Alarm" + "Broker",
        "alarm-" + "broker",
        "alarm_" + "broker",
        "ALARM_" + "BROKER",
    ],
)
def test_public_hygiene_rejects_legacy_brand_outside_migration_docs(
    tmp_path: Path, legacy_name: str
) -> None:
    candidate = tmp_path / "public.md"
    candidate.write_text(legacy_name, encoding="utf-8")

    assert _content_reason(candidate, "README.md") == (
        "legacy product identifier outside the migration allowlist"
    )


def test_public_hygiene_allows_legacy_brand_in_migration_docs(tmp_path: Path) -> None:
    candidate = tmp_path / "CHANGELOG.md"
    candidate.write_text("Alarm " + "Broker -> Escalane", encoding="utf-8")

    assert _content_reason(candidate, "CHANGELOG.md") is None


def test_public_hygiene_keeps_explicit_missing_candidates_for_fail_closed_check() -> None:
    assert _candidate_paths(["missing.txt", "missing.txt"]) == ["missing.txt"]


def test_public_hygiene_no_argument_mode_uses_repository_inventory(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.verify_public_hygiene._repository_candidates",
        lambda: ["tracked.txt"],
    )

    assert _candidate_paths([]) == ["tracked.txt"]


def test_public_hygiene_omits_deleted_paths_from_worktree_candidate() -> None:
    assert _worktree_paths(
        ["present.txt", "deleted.txt"],
        {"deleted.txt"},
    ) == ["present.txt"]


def test_public_hygiene_rejects_missing_candidate(tmp_path: Path) -> None:
    assert _inspection_reason(tmp_path / "missing.txt") == (
        "candidate path is missing or inaccessible"
    )


def test_public_hygiene_rejects_placeholder_in_curated_screenshot_slot(
    tmp_path: Path,
) -> None:
    screenshot = tmp_path / "01-admin-overview.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01")

    assert (
        _inspection_reason(screenshot, "docs/assets/screenshots/01-admin-overview.png")
        == "curated screenshot does not match the reviewed viewport dimensions"
    )
