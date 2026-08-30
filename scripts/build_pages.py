"""Build the GitHub Pages artifact from static demo source and live UI assets."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PAGES_SOURCE = REPOSITORY_ROOT / "pages"
PRODUCT_ASSETS = REPOSITORY_ROOT / "src" / "escalane" / "web" / "assets"
BUILD_ROOT = REPOSITORY_ROOT / "build"


def build(output: Path) -> None:
    """Create a clean Pages artifact while keeping product assets authoritative."""
    destination = _safe_output_destination(output)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(PAGES_SOURCE, destination)
    destination_assets = destination / "assets"
    for asset in PRODUCT_ASSETS.iterdir():
        if asset.is_file():
            shutil.copy2(asset, destination_assets / asset.name)
    (destination / ".nojekyll").touch()


def _safe_output_destination(output: Path) -> Path:
    """Resolve and reject destinations that could erase source or user data."""
    destination = output.resolve()
    if destination == REPOSITORY_ROOT.resolve() or _overlaps_source(destination):
        raise ValueError("Pages output must not overlap repository sources or product assets")
    if destination.exists() and not _is_managed_build_destination(destination):
        raise ValueError("Pages output must be new or inside the managed build directory")
    return destination


def _overlaps_source(destination: Path) -> bool:
    return any(
        _paths_overlap(destination, source)
        for source in (PAGES_SOURCE.resolve(), PRODUCT_ASSETS.resolve())
    )


def _paths_overlap(left: Path, right: Path) -> bool:
    return left.is_relative_to(right) or right.is_relative_to(left)


def _is_managed_build_destination(destination: Path) -> bool:
    build_root = BUILD_ROOT.resolve()
    return destination != build_root and destination.is_relative_to(build_root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "build" / "pages",
    )
    args = parser.parse_args()
    build(args.output.resolve())
    print(f"Built GitHub Pages artifact at {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
