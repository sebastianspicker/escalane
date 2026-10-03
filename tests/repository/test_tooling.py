"""Regression tests for local and CI tooling contracts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from scripts import check_constraints

ROOT = Path(__file__).resolve().parents[2]


def test_constraints_are_exact_and_consistent() -> None:
    assert check_constraints.validate() == []


def test_coverage_target_propagates_test_failure_with_temporary_python_shim(
    tmp_path: Path,
) -> None:
    virtual_environment = tmp_path / "venv"
    binary_directory = virtual_environment / "bin"
    binary_directory.mkdir(parents=True)
    shim_log = tmp_path / "python-arguments.log"
    python_shim = binary_directory / "python"
    python_shim.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "$SHIM_LOG"\n'
        "if [[ \"$*\" == '-m coverage run -m pytest -q -p no:cacheprovider' ]]; then\n"
        "  exit 23\n"
        "fi\n"
        "if [[ \"$*\" == '-m coverage report' ]]; then\n"
        "  exit 0\n"
        "fi\n"
        "exit 99\n"
    )
    python_shim.chmod(0o755)

    result = subprocess.run(  # noqa: S603
        ["make", "coverage", f"VENV={virtual_environment}"],
        cwd=ROOT,
        env={**os.environ, "SHIM_LOG": str(shim_log)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert shim_log.read_text().splitlines() == [
        "-m coverage run -m pytest -q -p no:cacheprovider",
        "-m coverage report",
    ]


def test_ordinary_pull_requests_run_the_container_job() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "if: github.event_name == 'pull_request' || inputs.release_smoke" in workflow


def test_ci_delegates_verification_to_make_targets() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    for target in (
        "constraints-check",
        "lint",
        "type-check",
        "architecture-check",
        "hygiene-check",
        "pages-build",
        "pages-check",
        "coverage",
        "audit",
        "package-check",
    ):
        assert target in workflow
    # make lint also runs ruff format --check, so CI does not invoke format-check separately.
    assert "ruff check" not in workflow
    assert "mypy" not in workflow
