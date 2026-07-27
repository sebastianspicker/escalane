"""Static contract tests for release, deployment, and screenshot GitHub workflows."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.repository


def _load_yaml(relative_path: str) -> dict[str, Any]:
    payload = yaml.safe_load((REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _named_step(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(step for step in steps if step.get("name") == name)


def test_release_builds_smokes_and_pushes_one_image() -> None:
    workflow = _load_yaml(".github/workflows/release.yml")
    steps = workflow["jobs"]["release"]["steps"]

    metadata = _named_step(steps, "Extract metadata")
    build = _named_step(steps, "Build the release image once")
    smoke = _named_step(steps, "Migrate and probe the exact release image")
    publish = _named_step(steps, "Push the tested image and record its digest")

    assert metadata["with"]["flavor"] == "latest=false"
    assert "refs/tags/v0." in metadata["with"]["tags"]
    assert build["with"]["load"] is True
    assert build["with"]["push"] is False
    assert "escalane:release-candidate" in build["with"]["tags"]
    assert smoke["run"] == ("bash scripts/smoke_container.sh escalane:release-candidate")
    assert "docker push" in publish["run"]
    assert "digest=" in publish["run"]
    assert steps.index(build) < steps.index(smoke) < steps.index(publish)


def test_release_rejects_lightweight_tags() -> None:
    workflow = _load_yaml(".github/workflows/release.yml")
    preflight_steps = workflow["jobs"]["preflight"]["steps"]
    ancestry = _named_step(preflight_steps, "Require the tagged commit to be on origin/main")

    assert 'git cat-file -t "${GITHUB_REF}"' in ancestry["run"]
    assert '= "tag"' in ancestry["run"]


def test_ci_and_release_share_the_container_smoke_contract() -> None:
    workflow = _load_yaml(".github/workflows/ci.yml")
    steps = workflow["jobs"]["build"]["steps"]
    smoke = _named_step(steps, "Migrate and probe the built image")

    assert smoke["run"] == "bash scripts/smoke_container.sh escalane:dev"


def test_compose_enables_redis_append_only_persistence() -> None:
    compose = _load_yaml("deploy/docker-compose.yml")
    command = compose["services"]["redis"]["command"]

    assert command[command.index("--appendonly") + 1] == "yes"
    assert command[command.index("--appendfsync") + 1] == "everysec"


def test_compose_uses_one_overridable_application_image() -> None:
    compose = _load_yaml("deploy/docker-compose.yml")
    services = compose["services"]
    expected_image = "${ESCALANE_IMAGE:-escalane:local}"

    assert {services[name]["image"] for name in ("migration", "api", "worker")} == {expected_image}


def test_public_docs_deploy_the_published_digest_without_source_pull() -> None:
    setup = (REPOSITORY_ROOT / "docs/SETUP.md").read_text(encoding="utf-8")
    releasing = (REPOSITORY_ROOT / "docs/RELEASING.md").read_text(encoding="utf-8")

    assert "git pull" not in setup
    for document in (setup, releasing):
        assert "ESCALANE_IMAGE" in document
        assert "ghcr.io/sebastianspicker/escalane@sha256:<digest>" in document
        assert "pull migration api worker" in document


def test_public_quickstarts_name_the_sample_seed_inputs() -> None:
    required_values = {
        "YEALINK_DEVICE_TOKEN",
        "SIGNAL_TARGET_GROUP_ID",
        "ESCALATE_T1",
        "ESCALATE_T2",
        "ESCALATE_T3",
    }

    for relative_path in ("README.md", "docs/SETUP.md"):
        document = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        assert required_values.issubset(set(re.findall(r"\b[A-Z][A-Z0-9_]+\b", document)))


def test_dependency_audit_does_not_suppress_the_fixed_pygments_issue() -> None:
    pyproject = tomllib.loads(
        (REPOSITORY_ROOT / "services/escalane/pyproject.toml").read_text(encoding="utf-8")
    )
    dev_dependencies = pyproject["project"]["optional-dependencies"]["dev"]
    workflow = _load_yaml(".github/workflows/ci.yml")
    security_steps = workflow["jobs"]["security"]["steps"]
    audit = _named_step(security_steps, "Run dependency vulnerability scan")

    assert "Pygments>=2.20.0" in dev_dependencies
    assert audit["run"] == "cd services/escalane && pip-audit"


def test_screenshot_workflow_is_review_only() -> None:
    workflow = _load_yaml(".github/workflows/screenshots.yml")
    steps = workflow["jobs"]["capture"]["steps"]
    capture = _named_step(steps, "Capture the Mock University gallery")
    upload = _named_step(steps, "Upload screenshots for manual review")
    shell_commands = "\n".join(str(step.get("run", "")) for step in steps if isinstance(step, dict))

    assert workflow["permissions"] == {"contents": "read"}
    assert "--admin-key" in capture["run"]
    assert upload["with"]["path"] == "screenshot-review/"
    assert upload["with"]["retention-days"] == 7
    assert "SOURCE_COMMIT.txt" in shell_commands
    assert "git commit" not in shell_commands
    assert "git push" not in shell_commands
    assert "gh release" not in shell_commands


def test_external_github_actions_are_pinned_to_full_commits() -> None:
    violations: list[str] = []
    for workflow_path in sorted((REPOSITORY_ROOT / ".github/workflows").glob("*.yml")):
        text = workflow_path.read_text(encoding="utf-8")
        for action in re.findall(r"^\s*uses:\s*(\S+)", text, flags=re.MULTILINE):
            if action.startswith("./"):
                continue
            _, separator, revision = action.rpartition("@")
            if not separator or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
                violations.append(f"{workflow_path.name}: {action}")

    assert violations == []


def test_public_markdown_links_resolve() -> None:
    markdown_files = [
        *REPOSITORY_ROOT.glob("*.md"),
        *(REPOSITORY_ROOT / "docs").rglob("*.md"),
        *(REPOSITORY_ROOT / ".github").rglob("*.md"),
        REPOSITORY_ROOT / "services" / "escalane" / "README.md",
    ]

    missing: list[str] = []
    for markdown_file in sorted(set(markdown_files)):
        text = markdown_file.read_text(encoding="utf-8")
        for match in re.finditer(r"!?\[[^\]]*\]\(([^)]+)\)", text):
            raw_target = match.group(1).strip().split(maxsplit=1)[0].strip("<>")
            if raw_target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            relative_target = unquote(raw_target.split("#", 1)[0].split("?", 1)[0])
            if not relative_target:
                continue
            resolved = (markdown_file.parent / relative_target).resolve()
            if not resolved.exists():
                missing.append(f"{markdown_file.relative_to(REPOSITORY_ROOT)} -> {raw_target}")

    assert missing == []
