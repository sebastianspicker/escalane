"""Static UI foundation tests for packaging, localization, and accessible styling."""

from __future__ import annotations

import tomllib
from importlib.resources import files
from pathlib import Path

import pytest

from escalane import __version__
from escalane.api.i18n import CATALOGUE, SUPPORTED_LOCALES, normalise_locale, translate

_ASSET_CSS_NAMES = (
    "ui.css",
    "tokens.css",
    "base.css",
    "shell.css",
    "worklist.css",
    "detail.css",
    "ack.css",
    "auth.css",
    "config.css",
    "a11y.css",
)

pytestmark = pytest.mark.repository


def _packaged_css() -> str:
    root = files("escalane.api").joinpath("assets")
    return "\n".join(root.joinpath(name).read_text("utf-8") for name in _ASSET_CSS_NAMES)


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
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    assert __version__ == "0.4.0-alpha.1"
    assert "version" in metadata["project"]["dynamic"]
    assert metadata["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "escalane.__version__"}


def test_i18n_normalises_and_falls_back() -> None:
    assert normalise_locale("de-DE") == "de"
    assert normalise_locale("fr") == "en"
    assert translate("missing", "de") == "missing"


def test_templates_use_shared_assets_and_no_inline_presentation() -> None:
    template_root = files("escalane.api").joinpath("templates")
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
    assert "/admin/assets/escalane-mark.svg" in base
    assert "brand_tagline" in base
    assert 'name="lang"' in macros
    assert 'name="locale"' not in macros
    assert "admin-nav" in base
    assert "has-session" in base


def test_required_browser_resources_are_packaged() -> None:
    package_root = files("escalane.api")
    for relative_path in (
        ("templates", "base.html"),
        ("templates", "ack.html"),
        ("templates", "admin_worklist.html"),
        ("assets", "ui.css"),
        ("assets", "tokens.css"),
        ("assets", "shell.css"),
        ("assets", "worklist.css"),
        ("assets", "ui.js"),
        ("assets", "escalane-mark.svg"),
    ):
        assert package_root.joinpath(*relative_path).is_file()


def test_ui_css_entry_imports_modules() -> None:
    css = files("escalane.api").joinpath("assets", "ui.css").read_text("utf-8")
    for name in _ASSET_CSS_NAMES[1:]:
        assert f'url("{name}")' in css or f"url('{name}')" in css


def test_assets_cover_motion_focus_and_narrow_worklist() -> None:
    css = _packaged_css()
    js = files("escalane.api").joinpath("assets", "ui.js").read_text("utf-8")
    assert "prefers-reduced-motion" in css
    assert "forced-colors" in css
    assert ":focus-visible" in css
    assert ".table-scroll" in css and "overflow-x: auto" in css
    assert "opener.focus()" in js


def test_operational_ui_avoids_decorative_motion_and_exposes_safe_actions() -> None:
    package_root = files("escalane.api")
    css = _packaged_css()
    js = package_root.joinpath("assets", "ui.js").read_text("utf-8")
    base = package_root.joinpath("templates", "base.html").read_text("utf-8")
    resources = package_root.joinpath("templates", "admin_resources.html").read_text("utf-8")
    worklist = package_root.joinpath("templates", "admin_worklist.html").read_text("utf-8")
    detail = package_root.joinpath("templates", "admin_detail.html").read_text("utf-8")

    assert "animateCount" not in js
    assert "IntersectionObserver" not in js
    assert "filter: blur" not in css
    assert "configuration_sections" in base
    assert "/admin/configuration/import" in base
    assert "data-confirm=\"{{ t('confirm_delete') }}\"" in resources
    assert ".logout-form { display: block; }" in css
    assert "--sidebar" in css or "--rail" in css
    # Age precedes ID in the worklist scan order.
    assert worklist.index("{{ t('age') }}") < worklist.index(">ID<")
    assert "action-panel" in detail


def test_text_and_action_tokens_meet_wcag_aa_contrast() -> None:
    css = _packaged_css()
    pairs = [
        ("#0c1b22", "#eef3f5"),
        ("#5a6e76", "#eef3f5"),
        ("#ffffff", "#0a6b7c"),
        ("#b42318", "#ffffff"),
        ("#edf4f6", "#0c171e"),
        ("#b6c4c9", "#0c171e"),
        ("#06242b", "#75d2e1"),
        ("#ffb4ab", "#12232d"),
    ]
    for foreground, background in pairs:
        assert _css_contains_hex(css, foreground), f"missing token {foreground}"
        assert _contrast(foreground, background) >= 4.5


def test_route_ledger_shell_classes_present() -> None:
    css = _packaged_css()
    assert ".has-session .site-header" in css or ".has-session" in css
    assert "--rail: #0b1f28" in css or "--rail:#0b1f28" in css
    assert (
        ".status-triggered" in css or ".status.status-triggered" in css or "status-triggered" in css
    )
