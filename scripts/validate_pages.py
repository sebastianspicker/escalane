"""Validate the static demo's scope, safety labels, and artifact links."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

REQUIRED_PAGES = {"index.html", "alarm.html", "acknowledge.html", "simulation.html"}
REQUIRED_ASSETS = {"assets/ui.css", "assets/ui.js", "assets/demo.css", "assets/demo.js"}
FORBIDDEN_ROUTE_PREFIXES = ("/admin", "/a/", "/v1/")
ALLOWED_EXTERNAL_SCHEMES = ("http", "https")


class DemoParser(HTMLParser):
    """Collect local links and verify command controls as HTML is parsed."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.command_depth = 0
        self.command_has_label: list[bool] = []
        self.violations: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        self._collect_links(values)
        if tag == "button":
            self._start_command(values)
            return
        self._mark_simulated_label(values)

    def _collect_links(self, values: dict[str, str | None]) -> None:
        for attribute in ("href", "src"):
            value = values.get(attribute)
            if value:
                self.links.append(value)

    def _start_command(self, values: dict[str, str | None]) -> None:
        if "data-simulated-action" not in values:
            self.violations.append("button is not marked data-simulated-action")
        self.command_depth += 1
        self.command_has_label.append(False)

    def _mark_simulated_label(self, values: dict[str, str | None]) -> None:
        if self.command_depth and "simulated-label" in (values.get("class") or "").split():
            self.command_has_label[-1] = True

    def handle_endtag(self, tag: str) -> None:
        if tag != "button" or not self.command_depth:
            return
        if not self.command_has_label.pop():
            self.violations.append("button has no visible Simulated label")
        self.command_depth -= 1


def validate(root: Path) -> list[str]:
    """Return every validation problem found under one built artifact root."""
    root = root.resolve()
    violations = _missing_artifact_violations(root)
    for page_name in sorted(REQUIRED_PAGES):
        violations.extend(_page_violations(root, page_name))
    return violations


def _missing_artifact_violations(root: Path) -> list[str]:
    inventory = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    missing = sorted((REQUIRED_PAGES | REQUIRED_ASSETS) - inventory)
    return [f"missing artifact: {path}" for path in missing]


def _page_violations(root: Path, page_name: str) -> list[str]:
    page = root / page_name
    if not page.exists():
        return []
    text = page.read_text(encoding="utf-8")
    parser = DemoParser()
    parser.feed(text)
    violations = [f"{page_name}: {message}" for message in parser.violations]
    for link in parser.links:
        violations.extend(_link_violations(root, page_name, page, link))
    if not _has_disclosure(text):
        violations.append(f"{page_name}: missing explicit static-demo disclosure")
    return violations


def _link_violations(root: Path, page_name: str, page: Path, link: str) -> list[str]:
    parsed = urlsplit(link)
    violations: list[str] = []
    if parsed.path.startswith(FORBIDDEN_ROUTE_PREFIXES):
        violations.append(f"{page_name}: live application route in static artifact: {link}")
    external_violations = _external_link_violations(page_name, link, parsed)
    if external_violations is not None:
        violations.extend(external_violations)
        return violations
    violations.extend(_local_target_violations(root, page_name, page, link, parsed))
    return violations


def _external_link_violations(page_name: str, link: str, parsed: SplitResult) -> list[str] | None:
    if parsed.scheme:
        if parsed.scheme in ALLOWED_EXTERNAL_SCHEMES:
            return []
        return [f"{page_name}: disallowed URL scheme in static artifact: {link}"]
    if parsed.netloc:
        return [f"{page_name}: protocol-relative link is not allowed: {link}"]
    return None


def _local_target_violations(
    root: Path, page_name: str, page: Path, link: str, parsed: SplitResult
) -> list[str]:
    if _skips_local_target_validation(parsed):
        return []
    target = (page.parent / parsed.path).resolve()
    if not target.is_relative_to(root):
        return [f"{page_name}: local target escapes static artifact: {link}"]
    if not target.exists():
        return [f"{page_name}: missing local target: {link}"]
    return []


def _skips_local_target_validation(parsed: SplitResult) -> bool:
    return bool(parsed.scheme or parsed.netloc or not parsed.path or parsed.path == ".")


def _has_disclosure(text: str) -> bool:
    disclosures = (
        "Static product demo",
        "Static simulation feed",
        "Responder simulation",
    )
    return any(disclosure in text for disclosure in disclosures)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    violations = validate(args.root.resolve())
    if violations:
        for violation in violations:
            print(violation)
        return 1
    print("Static demo validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
