from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.verify_public_hygiene import _content_reason, _path_reason  # noqa: E402


@pytest.mark.parametrize(
    ("relative_path", "expected_reason"),
    [
        (".env", "environment file"),
        ("docs/archive/report.md", "local-only workspace or report path"),
        ("private/notes.md", "local-only workspace or report path"),
        ("vendor/copied-source.py", "local-only workspace or report path"),
        ("backups/alarm.dump", "local-only workspace or report path"),
        ("service/build/output.txt", "generated dependency, cache, report, or build directory"),
        ("credentials.json", "local-only or credential filename"),
        ("capture.har", "sensitive or generated file extension"),
        ("deploy/docker-compose.override.yml", "local-only or credential filename"),
        ("docs/assets/screenshots/admin.png", "regenerated screenshot asset"),
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
        "services/alarm_broker/alarm_broker/api/assets/ui.css",
    ],
)
def test_public_hygiene_accepts_public_paths(relative_path: str) -> None:
    assert _path_reason(relative_path) is None


def test_public_hygiene_rejects_machine_specific_text(tmp_path: Path) -> None:
    candidate = tmp_path / "notes.txt"
    candidate.write_text('path="/' + 'Users/example/private"\n', encoding="utf-8")

    assert _content_reason(candidate) == "private key material or absolute user-home path"
