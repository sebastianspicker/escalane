"""Validate Escalane's exact Python 3.14 dependency constraints and consumers."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSTRAINT_DIR = ROOT / "constraints"
CONSTRAINT_PATHS = {
    "build": CONSTRAINT_DIR / "python314-build.txt",
    "runtime": CONSTRAINT_DIR / "python314-runtime.txt",
    "dev": CONSTRAINT_DIR / "python314-dev.txt",
}
PYTHON_PATCH = "3.14.7"
_PIN_PATTERN = re.compile(r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;]+)")
_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def canonical_name(name: str) -> str:
    """Return the normalized distribution name used for comparisons."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_constraints(path: Path) -> tuple[dict[str, str], list[str]]:
    """Read exact pins and return parse errors without accepting loose specifiers."""
    pins: dict[str, str] = {}
    errors: list[str] = []
    for line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN_PATTERN.fullmatch(line)
        if match is None:
            errors.append(f"{path.relative_to(ROOT)}:{line_number}: expected name==version")
            continue
        name = canonical_name(match.group("name"))
        if name in pins:
            errors.append(f"{path.relative_to(ROOT)}:{line_number}: duplicate pin for {name}")
        pins[name] = match.group("version")
    return pins, errors


def requirement_names(requirements: list[str]) -> set[str]:
    """Extract normalized names from PEP 508 requirements declared by this project."""
    names: set[str] = set()
    for requirement in requirements:
        match = _REQUIREMENT_NAME.match(requirement)
        if match is None:
            raise ValueError(f"Cannot parse requirement name: {requirement}")
        names.add(canonical_name(match.group()))
    return names


def _missing_names(label: str, required: set[str], pins: dict[str, str]) -> list[str]:
    missing = sorted(required - pins.keys())
    return [f"{label} constraints do not pin declared requirement: {name}" for name in missing]


def _check_python_version_consumers() -> list[str]:
    errors: list[str] = []
    makefile = (ROOT / "Makefile").read_text()
    configured_local = re.findall(r"^PYTHON_PATCH := ([0-9.]+)$", makefile, re.MULTILINE)
    if configured_local != [PYTHON_PATCH]:
        errors.append(f"Makefile Python version must be {PYTHON_PATCH}; found {configured_local}")

    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    configured_ci = re.findall(r'python-version: "([^"]+)"', workflow)
    if not configured_ci or set(configured_ci) != {PYTHON_PATCH}:
        errors.append(f"CI Python versions must all be {PYTHON_PATCH}; found {configured_ci}")

    dockerfile = (ROOT / "Dockerfile").read_text()
    configured_images = re.findall(r"^FROM python:([0-9.]+)-slim-trixie@", dockerfile, re.MULTILINE)
    if configured_images != [PYTHON_PATCH, PYTHON_PATCH]:
        errors.append(
            f"Docker builder and production Python versions must be {PYTHON_PATCH}; "
            f"found {configured_images}"
        )
    return errors


def validate() -> list[str]:
    """Return all deterministic constraint and consumer consistency errors."""
    errors: list[str] = []
    parsed: dict[str, dict[str, str]] = {}
    for label, path in CONSTRAINT_PATHS.items():
        pins, parse_errors = read_constraints(path)
        parsed[label] = pins
        errors.extend(parse_errors)

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    runtime_names = requirement_names(metadata["project"]["dependencies"])
    dev_names = runtime_names | requirement_names(
        metadata["project"]["optional-dependencies"]["dev"]
    )
    build_names = requirement_names(metadata["build-system"]["requires"]) | {"build", "pip"}
    errors.extend(_missing_names("runtime", runtime_names, parsed["runtime"]))
    errors.extend(_missing_names("development", dev_names, parsed["dev"]))
    errors.extend(_missing_names("build", build_names, parsed["build"]))

    for name, version in parsed["runtime"].items():
        if parsed["dev"].get(name) != version:
            errors.append(
                f"runtime pin {name}=={version} is not identical in development constraints"
            )

    expected_consumers = {
        "Makefile": ("python314-build.txt", "python314-runtime.txt", "python314-dev.txt"),
        ".github/workflows/ci.yml": (
            "python314-build.txt",
            "python314-runtime.txt",
            "python314-dev.txt",
        ),
        "Dockerfile": ("python314-build.txt", "python314-runtime.txt"),
    }
    for relative_path, filenames in expected_consumers.items():
        content = (ROOT / relative_path).read_text()
        for filename in filenames:
            if filename not in content:
                errors.append(f"{relative_path} does not consume {filename}")

    errors.extend(_check_python_version_consumers())
    return errors


def main() -> int:
    """Report constraint drift with a stable nonzero exit status."""
    errors = validate()
    if errors:
        print("Constraint validation failed:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("Python 3.14 build, runtime, and development constraints are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
