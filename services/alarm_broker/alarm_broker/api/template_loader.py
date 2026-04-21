from __future__ import annotations

from importlib.resources import files
from string import Template


def load_template(name: str) -> Template:
    try:
        return Template(files("alarm_broker.api").joinpath("templates", name).read_text("utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing packaged template resource: {name}") from exc
