"""Direct tests for the static Pages build and validation contracts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import build_pages  # noqa: E402
from scripts.validate_pages import validate  # noqa: E402

pytestmark = pytest.mark.repository


def _build_artifact(tmp_path: Path) -> Path:
    artifact = tmp_path / "pages"
    build_pages.build(artifact)
    return artifact


@pytest.mark.unit
def test_build_creates_a_valid_required_pages_artifact(tmp_path: Path) -> None:
    artifact = _build_artifact(tmp_path)

    assert (artifact / ".nojekyll").is_file()
    assert (artifact / "assets" / "ui.css").is_file()
    assert validate(artifact) == []


@pytest.mark.unit
def test_build_rejects_outputs_overlapping_sources_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    pages_source = repository / "pages"
    product_assets = repository / "services" / "product-assets"
    source_sentinel = pages_source / "source-sentinel.txt"
    asset_sentinel = product_assets / "asset-sentinel.txt"
    source_sentinel.parent.mkdir(parents=True)
    asset_sentinel.parent.mkdir(parents=True)
    source_sentinel.write_text("source stays intact", encoding="utf-8")
    asset_sentinel.write_text("assets stay intact", encoding="utf-8")
    monkeypatch.setattr(build_pages, "REPOSITORY_ROOT", repository)
    monkeypatch.setattr(build_pages, "PAGES_SOURCE", pages_source)
    monkeypatch.setattr(build_pages, "PRODUCT_ASSETS", product_assets)
    monkeypatch.setattr(build_pages, "BUILD_ROOT", repository / "build")

    for dangerous_output in (
        repository,
        pages_source,
        pages_source / "generated",
        product_assets.parent,
        product_assets,
    ):
        with pytest.raises(ValueError, match="must not overlap"):
            build_pages.build(dangerous_output)
        assert source_sentinel.read_text(encoding="utf-8") == "source stays intact"
        assert asset_sentinel.read_text(encoding="utf-8") == "assets stay intact"


@pytest.mark.unit
def test_build_rejects_existing_external_output_without_mutation(tmp_path: Path) -> None:
    output = tmp_path / "existing-output"
    sentinel = output / "sentinel.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("output stays intact", encoding="utf-8")

    with pytest.raises(ValueError, match="must be new"):
        build_pages.build(output)

    assert sentinel.read_text(encoding="utf-8") == "output stays intact"


@pytest.mark.unit
def test_validate_reports_a_missing_required_artifact(tmp_path: Path) -> None:
    artifact = _build_artifact(tmp_path)
    (artifact / "assets" / "demo.js").unlink()

    assert validate(artifact) == [
        "missing artifact: assets/demo.js",
        "acknowledge.html: missing local target: assets/demo.js",
        "alarm.html: missing local target: assets/demo.js",
        "index.html: missing local target: assets/demo.js",
        "simulation.html: missing local target: assets/demo.js",
    ]


@pytest.mark.unit
def test_validate_reports_a_forbidden_live_route(tmp_path: Path) -> None:
    artifact = _build_artifact(tmp_path)
    page = artifact / "index.html"
    page.write_text(page.read_text(encoding="utf-8") + '<a href="/admin/alarms">', encoding="utf-8")

    assert validate(artifact) == [
        "index.html: live application route in static artifact: /admin/alarms",
        "index.html: local target escapes static artifact: /admin/alarms",
    ]


@pytest.mark.unit
def test_validate_reports_a_broken_local_target(tmp_path: Path) -> None:
    artifact = _build_artifact(tmp_path)
    page = artifact / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8") + '<a href="missing.html">',
        encoding="utf-8",
    )

    assert validate(artifact) == ["index.html: missing local target: missing.html"]


@pytest.mark.unit
@pytest.mark.parametrize("link", ["javascript:alert(1)", "file:///outside.html"])
def test_validate_rejects_an_unsafe_url_scheme(tmp_path: Path, link: str) -> None:
    artifact = _build_artifact(tmp_path)
    page = artifact / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8") + f'<a href="{link}">',
        encoding="utf-8",
    )

    assert validate(artifact) == [f"index.html: disallowed URL scheme in static artifact: {link}"]


@pytest.mark.unit
def test_validate_rejects_a_local_target_that_escapes_the_artifact(tmp_path: Path) -> None:
    artifact = _build_artifact(tmp_path)
    outside_sentinel = tmp_path / "outside.html"
    outside_sentinel.write_text("must not be accepted", encoding="utf-8")
    page = artifact / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8") + '<a href="../outside.html">',
        encoding="utf-8",
    )

    assert outside_sentinel.exists()
    assert validate(artifact) == [
        "index.html: local target escapes static artifact: ../outside.html"
    ]


@pytest.mark.unit
def test_validate_allows_http_and_https_external_links(tmp_path: Path) -> None:
    artifact = _build_artifact(tmp_path)
    page = artifact / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8")
        + '<a href="http://example.test"><a href="https://example.test">',
        encoding="utf-8",
    )

    assert validate(artifact) == []


@pytest.mark.unit
def test_validate_rejects_a_protocol_relative_link(tmp_path: Path) -> None:
    artifact = _build_artifact(tmp_path)
    page = artifact / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8") + '<a href="//example.test/path">',
        encoding="utf-8",
    )

    assert validate(artifact) == [
        "index.html: protocol-relative link is not allowed: //example.test/path"
    ]


@pytest.mark.unit
def test_validate_requires_simulated_command_marker_and_label(tmp_path: Path) -> None:
    artifact = _build_artifact(tmp_path)
    page = artifact / "index.html"
    page.write_text(page.read_text(encoding="utf-8") + "<button>Run</button>", encoding="utf-8")

    assert validate(artifact) == [
        "index.html: button is not marked data-simulated-action",
        "index.html: button has no visible Simulated label",
    ]


@pytest.mark.unit
def test_validate_requires_a_static_demo_disclosure(tmp_path: Path) -> None:
    artifact = _build_artifact(tmp_path)
    page = artifact / "index.html"
    page.write_text("<main>Demo</main>", encoding="utf-8")

    assert validate(artifact) == ["index.html: missing explicit static-demo disclosure"]
