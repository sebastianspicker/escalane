"""Resolve exact Escalane dependency constraints with the active CPython 3.14."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSTRAINT_DIR = ROOT / "constraints"
EXCLUDED_PROJECT_PACKAGES = {"escalane", "pip", "setuptools", "wheel"}


def canonical_name(name: str) -> str:
    """Normalize a distribution name for stable, cross-platform output."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _run(python: Path, *arguments: str) -> None:
    subprocess.run([str(python), *arguments], cwd=ROOT, check=True)  # noqa: S603


def _resolve(requirements: tuple[str, ...], *, include_bootstrap: bool = False) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="escalane-constraints-") as temporary_directory:
        environment = Path(temporary_directory) / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        _run(python, "-m", "pip", "install", "--upgrade", *requirements)
        result = subprocess.run(  # noqa: S603
            [str(python), "-m", "pip", "list", "--format=json"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        packages = json.loads(result.stdout)

    excluded = set() if include_bootstrap else EXCLUDED_PROJECT_PACKAGES
    pins = {
        canonical_name(package["name"]): package["version"]
        for package in packages
        if canonical_name(package["name"]) not in excluded
    }
    return [f"{name}=={pins[name]}" for name in sorted(pins)]


def _write(filename: str, label: str, pins: list[str]) -> None:
    path = CONSTRAINT_DIR / filename
    rendered_pins = "\n".join(pins)
    content = (
        f"# Exact {label} environment for CPython 3.14 on supported macOS and Linux hosts.\n"
        "# Refresh with: make constraints-refresh\n"
        f"{rendered_pins}\n"
    )
    path.write_text(content)


def main() -> int:
    """Refresh all constraint lanes from independent clean environments."""
    parser = argparse.ArgumentParser()
    parser.parse_args()
    if sys.version_info[:2] != (3, 14):
        print("Constraint refresh requires CPython 3.14.", file=sys.stderr)
        return 2

    _write(
        "python314-build.txt",
        "build-tool",
        _resolve(("pip", "setuptools", "wheel", "build"), include_bootstrap=True),
    )
    _write("python314-runtime.txt", "runtime", _resolve((str(ROOT),)))
    _write("python314-dev.txt", "development", _resolve((f"{ROOT}[dev]",)))
    print("Refreshed exact CPython 3.14 build, runtime, and development constraints.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
