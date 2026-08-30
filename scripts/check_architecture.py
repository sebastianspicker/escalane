"""Enforce Escalane's one-way package import boundaries."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPOSITORY_ROOT / "src" / "escalane"
PACKAGE_NAMES = frozenset(
    {
        "alarms",
        "config",
        "configuration",
        "contracts",
        "notifications",
        "operations",
        "persistence",
        "providers",
        "runtime",
        "security",
        "web",
        "worker",
    }
)
# Cross-package dependencies deliberately present in the modular monolith.
# Imports within a package are always allowed and are not package edges.
ALLOWED_PACKAGE_EDGES: Mapping[str, frozenset[str]] = {
    "alarms": frozenset({"config", "contracts", "operations", "persistence", "runtime"}),
    "config": frozenset(),
    "configuration": frozenset({"config", "persistence"}),
    "contracts": frozenset(),
    "notifications": frozenset(
        {"config", "contracts", "operations", "persistence", "providers", "security"}
    ),
    "operations": frozenset({"contracts", "persistence"}),
    "persistence": frozenset({"config", "contracts"}),
    "providers": frozenset({"security"}),
    "runtime": frozenset(),
    "security": frozenset(),
    "web": frozenset(
        {
            "alarms",
            "config",
            "configuration",
            "contracts",
            "operations",
            "persistence",
            "providers",
            "runtime",
            "security",
        }
    ),
    "worker": frozenset(
        {
            "alarms",
            "config",
            "contracts",
            "notifications",
            "operations",
            "persistence",
            "providers",
            "security",
        }
    ),
}
REMOVED_NAMESPACES = frozenset(
    {"api", "connectors", "core", "db", "services", "settings", "types", "constants"}
)


@dataclass(frozen=True)
class ImportReference:
    """Record one resolved internal import for an actionable diagnostic."""

    namespace: str
    line: int


def _package_for(path: Path, package_root: Path = PACKAGE_ROOT) -> str | None:
    relative = path.relative_to(package_root)
    return relative.parts[0] if len(relative.parts) > 1 else None


def _relative_namespace(
    path: Path,
    level: int,
    module: str | None,
    package_root: Path = PACKAGE_ROOT,
) -> str | None:
    package_parts = list(path.relative_to(package_root).with_suffix("").parts[:-1])
    if level > len(package_parts) + 1:
        return None
    parent_parts = package_parts[: len(package_parts) - (level - 1)]
    target_parts = ["escalane", *parent_parts]
    if module:
        target_parts.extend(module.split("."))
    return ".".join(target_parts)


def _imports(path: Path, package_root: Path = PACKAGE_ROOT) -> list[ImportReference]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[ImportReference] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(
                ImportReference(alias.name, node.lineno)
                for alias in node.names
                if alias.name == "escalane" or alias.name.startswith("escalane.")
            )
        elif isinstance(node, ast.ImportFrom):
            namespace = (
                _relative_namespace(path, node.level, node.module, package_root)
                if node.level
                else node.module
            )
            if namespace == "escalane" or (
                namespace is not None and namespace.startswith("escalane.")
            ):
                imports.append(ImportReference(namespace, node.lineno))
    return imports


def _top_level(namespace: str) -> str | None:
    parts = namespace.split(".")
    return parts[1] if len(parts) > 1 else None


def _violation(source: str | None, target: str | None) -> str | None:
    if target in REMOVED_NAMESPACES:
        return f"imports removed namespace escalane.{target}"
    if source is None or target is None or source == target:
        return None
    if target not in PACKAGE_NAMES:
        return f"imports unknown package escalane.{target}"
    if target not in ALLOWED_PACKAGE_EDGES[source]:
        return f"package {source!r} may not import {target!r}"
    return None


def _cycles(edges: Mapping[str, set[str]]) -> list[tuple[str, ...]]:
    """Return deterministic back-edge cycles in the package dependency graph."""
    active: list[str] = []
    state: dict[str, int] = {package: 0 for package in sorted(edges)}
    cycles: list[tuple[str, ...]] = []

    def visit(package: str) -> None:
        state[package] = 1
        active.append(package)
        for dependency in sorted(edges[package]):
            if state[dependency] == 0:
                visit(dependency)
            elif state[dependency] == 1:
                start = active.index(dependency)
                cycles.append(tuple([*active[start:], dependency]))
        active.pop()
        state[package] = 2

    for package in sorted(edges):
        if state[package] == 0:
            visit(package)
    return cycles


def check(
    package_root: Path = PACKAGE_ROOT,
    repository_root: Path = REPOSITORY_ROOT,
) -> list[str]:
    """Return all import-boundary violations in deterministic path order."""
    violations: list[str] = []
    edges: dict[str, set[str]] = {package: set() for package in PACKAGE_NAMES}
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(repository_root)
        source = _package_for(path, package_root)
        try:
            imported_references = _imports(path, package_root)
        except SyntaxError as error:
            line = error.lineno or 1
            violations.append(f"{relative}:{line}: cannot parse Python source: {error.msg}")
            continue
        for imported in imported_references:
            target = _top_level(imported.namespace)
            reason = _violation(source, target)
            if reason:
                violations.append(f"{relative}:{imported.line}: {reason} ({imported.namespace})")
            if source in PACKAGE_NAMES and target in PACKAGE_NAMES and source != target:
                edges[source].add(target)
    violations.extend(f"import cycle: {' -> '.join(cycle)}" for cycle in _cycles(edges))
    return violations


def main() -> int:
    """Print violations and return a conventional check exit status."""
    if not PACKAGE_ROOT.is_dir():
        print(f"architecture check failed: package root is missing: {PACKAGE_ROOT}")
        return 1
    violations = check()
    if violations:
        print("Architecture import-boundary check failed:")
        print("\n".join(violations))
        return 1
    print("Architecture import-boundary check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
