"""Validate the repository metadata for a version-tagged release."""

from __future__ import annotations

import argparse
import ast
import re
import string
from collections.abc import Callable, Sequence
from pathlib import Path

ASCII_DIGITS = frozenset(string.digits)
SEMVER_IDENTIFIER_CHARS = frozenset(string.ascii_letters + string.digits + "-")


def _is_strict_numeric_identifier(value: str) -> bool:
    """Accept SemVer numeric identifiers while rejecting illegal leading zeroes."""
    return (
        bool(value)
        and all(char in ASCII_DIGITS for char in value)
        and (value == "0" or not value.startswith("0"))
    )


def _identifier_chars_are_valid(value: str) -> bool:
    """Require non-empty identifiers composed only of SemVer-safe characters."""
    return bool(value) and all(char in SEMVER_IDENTIFIER_CHARS for char in value)


def _prerelease_identifier_is_valid(value: str) -> bool:
    """Validate prerelease identifiers, including SemVer's numeric restriction."""
    if not _identifier_chars_are_valid(value):
        return False
    is_numeric = all(char in ASCII_DIGITS for char in value)
    return not is_numeric or _is_strict_numeric_identifier(value)


def _identifiers_are_valid(value: str, validator: Callable[[str], bool]) -> bool:
    """Validate every dot-separated identifier with the supplied rule."""
    return bool(value) and all(validator(identifier) for identifier in value.split("."))


def _optional_identifiers_are_valid(
    separator: str,
    value: str,
    validator: Callable[[str], bool],
) -> bool:
    """Validate an optional SemVer suffix only when its separator is present."""
    return not separator or _identifiers_are_valid(value, validator)


def _core_is_valid(core: str) -> bool:
    """Require exactly three strict numeric identifiers for the SemVer core."""
    identifiers = core.split(".")
    return len(identifiers) == 3 and all(
        _is_strict_numeric_identifier(identifier) for identifier in identifiers
    )


def version_from_tag(tag: str) -> tuple[str, bool]:
    """Return a strict SemVer tag's package version and prerelease status."""
    if not tag.startswith("v"):
        raise ValueError(f"release tag must be strict SemVer with a v prefix: {tag!r}")

    version = tag[1:]
    public_version, build_separator, build = version.partition("+")
    if not _optional_identifiers_are_valid(
        build_separator, build, _identifier_chars_are_valid
    ):
        raise ValueError(f"release tag must be strict SemVer with a v prefix: {tag!r}")

    core, prerelease_separator, prerelease = public_version.partition("-")
    if not _optional_identifiers_are_valid(
        prerelease_separator, prerelease, _prerelease_identifier_is_valid
    ):
        raise ValueError(f"release tag must be strict SemVer with a v prefix: {tag!r}")

    if not _core_is_valid(core):
        raise ValueError(f"release tag must be strict SemVer with a v prefix: {tag!r}")

    return version, bool(prerelease_separator)


def package_version(version_file: Path) -> str:
    """Read ``escalane.__version__`` without importing application dependencies."""
    tree = ast.parse(
        version_file.read_text(encoding="utf-8"), filename=str(version_file)
    )
    for statement in tree.body:
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and statement.targets[0].id == "__version__"
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            return statement.value.value
    raise ValueError(f"{version_file} must assign a literal string to __version__")


def changelog_has_release(changelog: Path, version: str) -> bool:
    """Return whether the changelog declares a level-two heading for ``version``."""
    heading = re.compile(
        rf"^##\s+\[?{re.escape(version)}\]?(?:\s+-\s+.+)?\s*$", re.MULTILINE
    )
    return heading.search(changelog.read_text(encoding="utf-8")) is not None


def validate_release(tag: str, version_file: Path, changelog: Path) -> tuple[str, bool]:
    """Validate tag, package version, and changelog declaration together."""
    version, prerelease = version_from_tag(tag)
    if package_version(version_file) != version:
        raise ValueError(
            f"tag version {version!r} does not match {version_file} __version__ "
            f"{package_version(version_file)!r}"
        )
    if not changelog_has_release(changelog, version):
        raise ValueError(f"{changelog} has no release heading for {version!r}")
    return version, prerelease


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse release inputs without reading repository files prematurely."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--version-file",
        type=Path,
        default=Path("services/escalane/escalane/__init__.py"),
    )
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--github-output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate release metadata and optionally emit GitHub Actions outputs."""
    args = parse_args(argv)
    try:
        version, prerelease = validate_release(
            args.tag, args.version_file, args.changelog
        )
    except (OSError, SyntaxError, ValueError) as error:
        print(f"release validation failed: {error}")
        return 1

    outputs = f"version={version}\nprerelease={'true' if prerelease else 'false'}\n"
    if args.github_output is None:
        print(outputs, end="")
    else:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(outputs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
