"""Release metadata validation tests for semantic tags and changelog alignment."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[4] / "scripts" / "validate_release.py"
SPEC = importlib.util.spec_from_file_location("validate_release", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
validate_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_release)

pytestmark = pytest.mark.repository


@pytest.mark.parametrize(
    ("tag", "version", "prerelease"),
    [
        ("v1.2.3", "1.2.3", False),
        ("v1.2.3-rc.1", "1.2.3-rc.1", True),
        ("v1.2.3-beta.4", "1.2.3-beta.4", True),
        ("v1.2.3-alpha.0+build.7", "1.2.3-alpha.0+build.7", True),
    ],
)
def test_version_from_tag_accepts_strict_semver(tag: str, version: str, prerelease: bool) -> None:
    assert validate_release.version_from_tag(tag) == (version, prerelease)


@pytest.mark.parametrize(
    "tag",
    [
        "1.2.3",
        "v01.2.3",
        "v1.2",
        "v1.2.3.4",
        "v1.2.3-01",
        "v1.2.3-rc..1",
        "v1.2.3-rc_1",
        "v1.2.3-\u03b2",
        "v1.2.3-",
        "v1.2.3+build..1",
        "v1.2.3+build+again",
    ],
)
def test_version_from_tag_rejects_non_semver_tags(tag: str) -> None:
    with pytest.raises(ValueError, match="strict SemVer"):
        validate_release.version_from_tag(tag)


def test_version_from_tag_rejects_long_invalid_identifier() -> None:
    with pytest.raises(ValueError, match="strict SemVer"):
        validate_release.version_from_tag("v1.2.3-" + ("a" * 100_000) + "!")


def test_validate_release_requires_matching_version_and_changelog(tmp_path: Path) -> None:
    version_file = tmp_path / "__init__.py"
    changelog = tmp_path / "CHANGELOG.md"
    version_file.write_text('__version__ = "1.2.3-rc.1"\n', encoding="utf-8")
    changelog.write_text("# Changelog\n\n## [1.2.3-rc.1] - 2026-07-14\n", encoding="utf-8")

    assert validate_release.validate_release("v1.2.3-rc.1", version_file, changelog) == (
        "1.2.3-rc.1",
        True,
    )


def test_validate_release_accepts_undated_candidate_heading(tmp_path: Path) -> None:
    version_file = tmp_path / "__init__.py"
    changelog = tmp_path / "CHANGELOG.md"
    version_file.write_text('__version__ = "1.2.3-alpha.1"\n', encoding="utf-8")
    changelog.write_text("# Changelog\n\n## [1.2.3-alpha.1]\n", encoding="utf-8")

    assert validate_release.validate_release("v1.2.3-alpha.1", version_file, changelog) == (
        "1.2.3-alpha.1",
        True,
    )


def test_validate_release_rejects_missing_changelog_heading(tmp_path: Path) -> None:
    version_file = tmp_path / "__init__.py"
    changelog = tmp_path / "CHANGELOG.md"
    version_file.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    changelog.write_text("# Changelog\n\n## [1.2.2] - 2026-07-14\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no release heading"):
        validate_release.validate_release("v1.2.3", version_file, changelog)


def test_validate_release_rejects_package_version_mismatch(tmp_path: Path) -> None:
    version_file = tmp_path / "__init__.py"
    changelog = tmp_path / "CHANGELOG.md"
    version_file.write_text('__version__ = "1.2.2"\n', encoding="utf-8")
    changelog.write_text("# Changelog\n\n## [1.2.3] - 2026-07-14\n", encoding="utf-8")

    with pytest.raises(ValueError, match="does not match"):
        validate_release.validate_release("v1.2.3", version_file, changelog)
