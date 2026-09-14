"""Contracts for the self-contained GitHub Pages demo artifact."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.build_pages import build
from scripts.validate_pages import validate

pytestmark = pytest.mark.unit


def test_pages_build_is_complete_and_valid(tmp_path: Path) -> None:
    output = tmp_path / "pages"

    build(output)

    assert (output / ".nojekyll").is_file()
    assert validate(output) == []


def test_pages_validator_rejects_unhandled_simulated_form(tmp_path: Path) -> None:
    output = tmp_path / "pages"
    build(output)
    index = output / "index.html"
    index.write_text(
        index.read_text(encoding="utf-8").replace(
            "</main>",
            '<form><button type="submit" data-simulated-action>'
            '<span class="simulated-label">Simulated</span></button></form></main>',
        ),
        encoding="utf-8",
    )

    violations = validate(output)

    assert "index.html: form has no recognized browser-local submit handler" in violations
