"""Contracts for maintained repository documentation."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _documentation_targets(text: str) -> set[str]:
    markdown_links = {
        match.group(1).strip() for match in re.finditer(r"\[[^\]]+\]\(([^)]+\.md)\)", text)
    }
    backtick_paths = {
        parts[1]
        for line in text.splitlines()
        if len(parts := line.split("`")) >= 3 and parts[1].endswith(".md")
    }
    return markdown_links | backtick_paths


def test_documentation_index_links_exist() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    documentation_root = repository_root / "docs"
    text = (documentation_root / "README.md").read_text(encoding="utf-8")

    for target in sorted(_documentation_targets(text)):
        assert (documentation_root / target).exists(), f"Missing documentation: {target}"
