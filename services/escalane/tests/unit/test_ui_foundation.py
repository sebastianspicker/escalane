from __future__ import annotations

import tomllib
from importlib.resources import files
from pathlib import Path

from alarm_broker import __version__
from alarm_broker.api.i18n import CATALOGUE, SUPPORTED_LOCALES, normalise_locale, translate


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    brighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (brighter + 0.05) / (darker + 0.05)


def _css_contains_hex(css: str, color: str) -> bool:
    expanded = color.lower()
    shortened = "#" + "".join(expanded[index] for index in (1, 3, 5))
    if all(expanded[index] == expanded[index + 1] for index in (1, 3, 5)):
        return expanded in css.lower() or shortened in css.lower()
    return expanded in css.lower()


def test_translation_catalogue_has_exact_key_parity() -> None:
    expected = set(CATALOGUE[SUPPORTED_LOCALES[0]])
    assert all(set(CATALOGUE[locale]) == expected for locale in SUPPORTED_LOCALES)


def test_package_version_matches_project_metadata() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    assert __version__ == "0.2.0"
    assert "version" in metadata["project"]["dynamic"]
    assert metadata["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "alarm_broker.__version__"
    }


def test_i18n_normalises_and_falls_back() -> None:
    assert normalise_locale("de-DE") == "de"
    assert normalise_locale("fr") == "en"
    assert translate("missing", "de") == "missing"


def test_templates_use_shared_assets_and_no_inline_presentation() -> None:
    template_root = files("alarm_broker.api").joinpath("templates")
    for name in (
        "base.html",
        "admin_login.html",
        "admin_worklist.html",
        "admin_detail.html",
        "ack.html",
        "error.html",
    ):
        source = template_root.joinpath(name).read_text("utf-8")
        assert "<style" not in source
        assert "<script" not in source or name == "base.html"
    base = template_root.joinpath("base.html").read_text("utf-8")
    macros = template_root.joinpath("macros.html").read_text("utf-8")
    assert "/admin/assets/ui.css" in base
    assert "/admin/assets/ui.js" in base
    assert 'name="lang"' in macros
    assert 'name="locale"' not in macros


def test_required_browser_resources_are_packaged() -> None:
    package_root = files("alarm_broker.api")
    for relative_path in (
        ("templates", "base.html"),
        ("templates", "ack.html"),
        ("templates", "admin_worklist.html"),
        ("assets", "ui.css"),
        ("assets", "ui.js"),
    ):
        assert package_root.joinpath(*relative_path).is_file()


def test_assets_cover_motion_focus_and_narrow_worklist() -> None:
    css = files("alarm_broker.api").joinpath("assets", "ui.css").read_text("utf-8")
    js = files("alarm_broker.api").joinpath("assets", "ui.js").read_text("utf-8")
    assert "prefers-reduced-motion" in css
    assert "forced-colors" in css
    assert ":focus-visible" in css
    assert ".table-scroll" in css and "overflow-x: auto" in css
    assert "opener.focus()" in js


def test_text_and_action_tokens_meet_wcag_aa_contrast() -> None:
    css = files("alarm_broker.api").joinpath("assets", "ui.css").read_text("utf-8")
    pairs = [
        ("#162029", "#f6f7f8"),
        ("#55616c", "#f6f7f8"),
        ("#ffffff", "#075c8f"),
        ("#a32727", "#ffffff"),
        ("#edf3f5", "#11171b"),
        ("#b5c0c7", "#11171b"),
        ("#071c27", "#70c7f4"),
        ("#ffb4ab", "#192126"),
    ]
    for foreground, background in pairs:
        assert _css_contains_hex(css, foreground)
        assert _contrast(foreground, background) >= 4.5
