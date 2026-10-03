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
        "notifications",
        "operations",
        "persistence",
        "providers",
        "runtime",
        "security",
        "telemetry",
        "web",
        "worker",
    }
)
# Cross-package dependencies deliberately present in the modular monolith.
# Imports within a package are always allowed and are not package edges.
ALLOWED_PACKAGE_EDGES: Mapping[str, frozenset[str]] = {
    "alarms": frozenset({"config", "persistence", "runtime", "telemetry"}),
    "config": frozenset(),
    "configuration": frozenset({"config", "persistence"}),
    "notifications": frozenset(
        {"alarms", "config", "persistence", "providers", "security", "telemetry"}
    ),
    "operations": frozenset({"persistence", "telemetry"}),
    "persistence": frozenset({"config", "telemetry"}),
    "providers": frozenset({"security"}),
    "runtime": frozenset(),
    "security": frozenset(),
    "telemetry": frozenset(),
    "web": frozenset(
        {
            "alarms",
            "config",
            "configuration",
            "operations",
            "persistence",
            "providers",
            "runtime",
            "security",
            "telemetry",
        }
    ),
    "worker": frozenset(
        {
            "alarms",
            "config",
            "notifications",
            "operations",
            "persistence",
            "providers",
            "security",
            "telemetry",
        }
    ),
}
REMOVED_NAMESPACES = frozenset(
    {
        "api",
        "connectors",
        "constants",
        "contracts",
        "core",
        "db",
        "services",
        "settings",
        "types",
    }
)
# HTTP frameworks are an inbound-adapter concern.
FRAMEWORK_MODULES = frozenset({"fastapi", "starlette"})
FRAMEWORK_PACKAGES = frozenset({"web"})
# Inbound adapters delegate queries and transactions to feature modules; they may
# hold an AsyncSession but not build SQL or drive the session directly.
PERSISTENCE_FREE_ADAPTERS = frozenset({"web", "worker"})
# SQLAlchemy submodules an adapter may import, per adapter. Everything under
# sqlalchemy.ext.asyncio (the session type) is allowed. The worker additionally
# imports sqlalchemy.exc because it classifies SQLAlchemyError for retries; that
# is error handling, not SQL construction.
ADAPTER_SQLALCHEMY_ALLOWED: Mapping[str, tuple[str, ...]] = {
    "web": ("sqlalchemy.ext.asyncio",),
    "worker": ("sqlalchemy.ext.asyncio", "sqlalchemy.exc"),
}
SESSION_DATA_CALLS = frozenset(
    {"add", "add_all", "commit", "delete", "execute", "flush", "get", "refresh", "rollback"}
    | {"scalar", "scalars"}
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


def _external_modules(tree: ast.AST) -> list[tuple[str, int]]:
    """Return absolute third-party module names imported anywhere in one module."""
    modules: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append((node.module, node.lineno))
    return modules


def _private_imports(tree: ast.AST) -> list[tuple[str, str, int]]:
    """Return underscore-prefixed names imported from other Escalane modules."""
    found: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module == "escalane" or node.module.startswith("escalane."):
                found.extend(
                    (node.module, alias.name, node.lineno)
                    for alias in node.names
                    if alias.name.startswith("_") and not alias.name.startswith("__")
                )
    return found


def _mentions_async_session(annotation: ast.expr | None) -> bool:
    """Return whether an annotation names AsyncSession in any spelling."""
    if annotation is None:
        return False
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name) and node.id == "AsyncSession":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "AsyncSession":
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                parsed = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                continue
            if _mentions_async_session(parsed):
                return True
    return False


def _session_names(tree: ast.AST) -> set[str]:
    """Return names that hold an AsyncSession: `session` or annotated parameters."""
    names = {"session"}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            arguments = node.args
            for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
                if _mentions_async_session(argument.annotation):
                    names.add(argument.arg)
            for variadic in (arguments.vararg, arguments.kwarg):
                if variadic is not None and _mentions_async_session(variadic.annotation):
                    names.add(variadic.arg)
    return names


def _persistence_access(tree: ast.AST, package: str) -> list[tuple[str, int]]:
    """Return disallowed SQLAlchemy imports and direct session data calls."""
    allowed = ADAPTER_SQLALCHEMY_ALLOWED[package]
    found: list[tuple[str, int]] = []
    for module, line in _external_modules(tree):
        if (module == "sqlalchemy" or module.startswith("sqlalchemy.")) and not any(
            module == prefix or module.startswith(f"{prefix}.") for prefix in allowed
        ):
            found.append((f"imports {module}", line))
    session_names = _session_names(tree)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in session_names
            and node.func.attr in SESSION_DATA_CALLS
        ):
            found.append((f"calls {node.func.value.id}.{node.func.attr}()", node.lineno))
    return found


def _module_rules(path: Path, tree: ast.AST, package_root: Path, relative: Path) -> list[str]:
    """Apply per-module adapter rules that complement the package edge table."""
    violations: list[str] = []
    source = _package_for(path, package_root)
    if source not in FRAMEWORK_PACKAGES:
        for module, line in _external_modules(tree):
            if module.split(".")[0] in FRAMEWORK_MODULES:
                violations.append(
                    f"{relative}:{line}: package {source!r} may not import HTTP framework {module}"
                )
    if source in PERSISTENCE_FREE_ADAPTERS:
        violations.extend(
            f"{relative}:{line}: {source} adapter {detail}; delegate to a feature module"
            for detail, line in _persistence_access(tree, source)
        )
    violations.extend(
        f"{relative}:{line}: imports private name {name} from {module}"
        for module, name, line in _private_imports(tree)
    )
    return violations


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
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported_references = _imports(path, package_root)
        except SyntaxError as error:
            line = error.lineno or 1
            violations.append(f"{relative}:{line}: cannot parse Python source: {error.msg}")
            continue
        violations.extend(_module_rules(path, tree, package_root, relative))
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
