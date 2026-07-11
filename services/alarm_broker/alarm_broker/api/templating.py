"""Shared autoescaped Jinja renderer for browser-facing HTML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).with_name("templates")
environment = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(("html", "xml")),
    enable_async=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_template(name: str, **context: Any) -> str:
    rendered = environment.get_template(name).render(**context)
    return str(rendered)
