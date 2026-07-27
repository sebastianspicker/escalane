"""Focused production configuration validation tests."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from escalane.api.main import create_app
from escalane.api.routes.health import EXPECTED_ALEMBIC_HEAD
from escalane.settings import Settings
from escalane.worker.settings import startup as worker_startup

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

try:
    from tests.constants import TEST_ADMIN_API_KEY, TEST_ZAMMAD_TOKEN
except ModuleNotFoundError:
    from constants import TEST_ADMIN_API_KEY, TEST_ZAMMAD_TOKEN


WEBHOOK_SECRET = "w" * 32
pytestmark = pytest.mark.unit


def _production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+asyncpg://alarm:strong-password@db:5432/alarm",
        "admin_api_key": TEST_ADMIN_API_KEY,
        "yelk_ip_allowlist": "203.0.113.0/24",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"sendxms_enabled": True}, "SENDXMS_API_KEY"),
        ({"signal_enabled": True}, "SIGNAL_TARGET_GROUP_ID"),
        ({"webhook_enabled": True}, "WEBHOOK_URL"),
        (
            {"webhook_enabled": True, "webhook_url": "https://hooks.example.test/events"},
            "WEBHOOK_ALLOWED_HOSTS",
        ),
        (
            {
                "webhook_enabled": True,
                "webhook_url": "https://hooks.example.test/events",
                "webhook_allowed_hosts": "other.example.test",
            },
            "WEBHOOK_URL host",
        ),
        (
            {
                "webhook_enabled": True,
                "webhook_url": "http://hooks.example.test/events",
                "webhook_allowed_hosts": "hooks.example.test",
            },
            "WEBHOOK_URL must use HTTPS",
        ),
        (
            {
                "webhook_enabled": True,
                "webhook_url": "https://hooks.example.test/events",
                "webhook_allowed_hosts": "hooks.example.test",
            },
            "WEBHOOK_SECRET",
        ),
    ],
)
def test_production_rejects_incomplete_enabled_connectors(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _production_settings(**overrides)


def test_production_accepts_complete_enabled_connectors() -> None:
    settings = _production_settings(
        sendxms_enabled=True,
        sendxms_api_key="sendxms-key",
        sendxms_base_url="https://sms.example.test",
        signal_enabled=True,
        signal_target_group_id="group-id",
        webhook_enabled=True,
        webhook_url="https://hooks.example.test/events",
        webhook_allowed_hosts="hooks.example.test,backup.example.test",
        webhook_secret=WEBHOOK_SECRET,
    )

    expect(settings.is_sms_enabled())
    expect(settings.is_signal_enabled())
    expect(settings.is_webhook_enabled())


def test_production_rejects_whitespace_only_webhook_secret() -> None:
    with pytest.raises(ValidationError, match="WEBHOOK_SECRET"):
        _production_settings(
            webhook_enabled=True,
            webhook_url="https://hooks.example.test/events",
            webhook_allowed_hosts="hooks.example.test",
            webhook_secret=" " * 32,
        )


def test_production_rejects_wildcard_webhook_allowlist() -> None:
    with pytest.raises(ValidationError, match="exact host"):
        _production_settings(webhook_allowed_hosts="*.example.test")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"zammad_api_token": TEST_ZAMMAD_TOKEN}, "ZAMMAD_BASE_URL"),
        (
            {"sendxms_enabled": True, "sendxms_api_key": "sendxms-key"},
            "SENDXMS_BASE_URL",
        ),
    ],
)
def test_production_rejects_activated_reserved_connector_endpoints(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _production_settings(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"zammad_api_token": TEST_ZAMMAD_TOKEN, "zammad_base_url": "zammad.example.test"},
        {
            "sendxms_enabled": True,
            "sendxms_api_key": "sendxms-key",
            "sendxms_base_url": "ftp://sms.example.test",
        },
        {
            "signal_enabled": True,
            "signal_target_group_id": "group-id",
            "signal_cli_endpoint": "http:///missing-host",
        },
        {
            "webhook_enabled": True,
            "webhook_url": "ftp://hooks.example.test/events",
            "webhook_allowed_hosts": "hooks.example.test",
        },
    ],
)
def test_production_rejects_activated_connector_urls_without_http_host(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="must be an http/https URL with a hostname"):
        _production_settings(**overrides)


def test_production_accepts_non_reserved_activated_connector_endpoints() -> None:
    settings = _production_settings(
        zammad_api_token=TEST_ZAMMAD_TOKEN,
        zammad_base_url="https://zammad.example.test",
        sendxms_enabled=True,
        sendxms_api_key="sendxms-key",
        sendxms_base_url="https://sms.example.test",
    )

    expect(bool(settings.zammad_api_token))
    expect(settings.is_sms_enabled())


@pytest.mark.parametrize(
    "overrides",
    [
        {"zammad_api_token": TEST_ZAMMAD_TOKEN, "zammad_base_url": "http://zammad.example.test"},
        {
            "sendxms_enabled": True,
            "sendxms_api_key": "sendxms-key",
            "sendxms_base_url": "http://sms.example.test",
        },
    ],
)
def test_production_rejects_cleartext_credential_connectors(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="must use HTTPS"):
        _production_settings(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "zammad_api_token": TEST_ZAMMAD_TOKEN,
            "zammad_base_url": "https://user:password@zammad.example.test",
        },
        {
            "sendxms_enabled": True,
            "sendxms_api_key": "sendxms-key",
            "sendxms_base_url": "https://user:password@sms.example.test",
        },
        {
            "webhook_enabled": True,
            "webhook_url": "https://user:password@hooks.example.test/events",
            "webhook_allowed_hosts": "hooks.example.test",
            "webhook_secret": WEBHOOK_SECRET,
        },
    ],
)
def test_production_rejects_connector_url_credentials(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="must not include URL credentials"):
        _production_settings(**overrides)


def test_simulation_keeps_incomplete_connector_configuration_explicit() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://alarm:change-me@db:5432/alarm",
        simulation_enabled=True,
        sendxms_enabled=True,
        signal_enabled=True,
        webhook_enabled=True,
    )

    expect(not settings.is_sms_enabled())
    expect(not settings.is_signal_enabled())
    expect(not settings.is_webhook_enabled())


def test_production_rejects_explicit_default_database_password() -> None:
    with pytest.raises(ValidationError, match="default DATABASE_URL password"):
        _production_settings(database_url="postgresql+asyncpg://alarm:change-me@db:5432/alarm")


def test_production_rejects_url_encoded_default_database_password() -> None:
    with pytest.raises(ValidationError, match="default DATABASE_URL password"):
        _production_settings(database_url="postgresql+asyncpg://alarm:change%2Dme@db:5432/alarm")


def test_runtime_rejects_implicit_default_database_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(
        _env_file=None,
        admin_api_key=TEST_ADMIN_API_KEY,
        yelk_ip_allowlist="203.0.113.0/24",
    )

    with pytest.raises(ValueError, match="default DATABASE_URL password"):
        settings.validate_runtime_configuration()


def test_simulation_runtime_allows_the_inert_default_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(_env_file=None, simulation_enabled=True)

    settings.validate_runtime_configuration()


def test_runtime_rejects_empty_yealink_allowlist_outside_simulation() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://alarm:strong-password@db:5432/alarm",
        admin_api_key=TEST_ADMIN_API_KEY,
        yelk_ip_allowlist="",
    )

    with pytest.raises(ValueError, match="YELK_IP_ALLOWLIST is required"):
        settings.validate_runtime_configuration()


def test_simulation_runtime_allows_empty_yealink_allowlist() -> None:
    settings = Settings(simulation_enabled=True, yelk_ip_allowlist="")

    settings.validate_runtime_configuration()


def test_simulation_rejects_non_loopback_base_url() -> None:
    with pytest.raises(ValidationError, match="BASE_URL must use a loopback host"):
        Settings(simulation_enabled=True, base_url="https://demo.example.test")


@pytest.mark.parametrize(
    "base_url",
    [
        "alarm.example.test",
        "ftp://alarm.example.test",
        "https://user:password@alarm.example.test",
        "https://alarm.example.test/prefix",
        "https://alarm.example.test?mode=test",
        "https://alarm.example.test#fragment",
        "http://alarm.example.test",
        "https://alarm.example.test:",
        "https://alarm example.test",
        "https://alarm.example.test\\evil.test",
    ],
)
def test_base_url_rejects_values_that_are_not_secure_origins(base_url: str) -> None:
    with pytest.raises(ValidationError, match="BASE_URL"):
        Settings(simulation_enabled=False, base_url=base_url)


def test_base_url_normalizes_one_origin_trailing_slash() -> None:
    settings = Settings(simulation_enabled=True, base_url="http://localhost:8080/")

    expect(settings.base_url == "http://localhost:8080")


async def test_api_lifespan_rejects_implicit_default_database_password(
    monkeypatch: pytest.MonkeyPatch,
    engine,
    fake_redis,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(
        _env_file=None,
        admin_api_key=TEST_ADMIN_API_KEY,
        yelk_ip_allowlist="203.0.113.0/24",
    )
    app = create_app(
        settings=settings,
        injected_engine=engine,
        injected_redis=fake_redis,
    )

    with pytest.raises(ValueError, match="default DATABASE_URL password"):
        async with app.router.lifespan_context(app):
            pass


async def test_worker_startup_rejects_implicit_default_database_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(
        _env_file=None,
        admin_api_key=TEST_ADMIN_API_KEY,
        yelk_ip_allowlist="203.0.113.0/24",
    )
    monkeypatch.setattr("escalane.worker.settings.get_settings", lambda: settings)

    with pytest.raises(ValueError, match="default DATABASE_URL password"):
        await worker_startup({})


def test_missing_admin_key_warning_describes_actual_forbidden_response() -> None:
    with pytest.warns(UserWarning, match="API endpoints return 403; browser login returns 500"):
        Settings(simulation_enabled=True)


def test_expected_alembic_head_matches_the_single_packaged_migration_head() -> None:
    """A new migration must update the health readiness expectation."""
    versions_dir = Path(__file__).parents[2] / "alembic" / "versions"
    revisions: set[str] = set()
    referenced_revisions: set[str] = set()
    for migration_path in versions_dir.glob("*.py"):
        assignments = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in ast.parse(migration_path.read_text()).body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"revision", "down_revision"}
        }
        revisions.add(assignments["revision"])
        down_revision = assignments["down_revision"]
        if isinstance(down_revision, str):
            referenced_revisions.add(down_revision)
        elif isinstance(down_revision, tuple):
            referenced_revisions.update(down_revision)

    heads = revisions - referenced_revisions
    expect(heads == {EXPECTED_ALEMBIC_HEAD})
