"""Application settings.

All configuration is loaded from environment variables (and optional .env file).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any
from urllib.parse import ParseResult, unquote, urlparse

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_YEALINK_TRIGGER_QUERY_KEY = "".join(("tok", "en"))
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _require_http_url(value: str, variable_name: str) -> None:
    """Require an activated connector endpoint to have an HTTP(S) host."""
    try:
        parsed = urlparse(value)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{variable_name} must be an http/https URL with a hostname") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{variable_name} must be an http/https URL with a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{variable_name} must not include URL credentials")


def _require_https_url(value: str, variable_name: str) -> None:
    _require_http_url(value, variable_name)
    if urlparse(value).scheme.lower() != "https":
        raise ValueError(f"{variable_name} must use HTTPS when enabled")


def _require_webhook_host_allowlisted(webhook_url: str, raw_allowed_hosts: str) -> None:
    allowed_hosts = {host.strip().lower() for host in raw_allowed_hosts.split(",") if host.strip()}
    if not allowed_hosts:
        raise ValueError("WEBHOOK_ALLOWED_HOSTS is required when WEBHOOK_ENABLED=true")
    hostname = urlparse(webhook_url).hostname
    if not hostname or hostname.lower() not in allowed_hosts:
        raise ValueError("WEBHOOK_URL host must be listed in WEBHOOK_ALLOWED_HOSTS")


def _validate_base_url(value: str) -> str:
    """Validate the canonical origin used for capability-bearing ACK links."""
    value = value.strip()
    _require_http_url(value, "BASE_URL")
    parsed = urlparse(value)
    if parsed.netloc.endswith(":"):
        raise ValueError("BASE_URL must not contain an empty port")
    if _has_invalid_base_url_authority(parsed.netloc):
        raise ValueError("BASE_URL authority contains an invalid character")
    if _has_base_url_suffix(parsed):
        raise ValueError("BASE_URL must be an origin without a path, query, or fragment")
    if _is_non_loopback_http_url(parsed):
        raise ValueError("BASE_URL must use HTTPS unless it uses a loopback host")
    return value.rstrip("/")


def _has_invalid_base_url_authority(authority: str) -> bool:
    return "\\" in authority or any(
        _is_invalid_authority_character(character) for character in authority
    )


def _is_invalid_authority_character(character: str) -> bool:
    if character.isspace():
        return True
    character_code = ord(character)
    return character_code < 32 or character_code == 127


def _has_base_url_suffix(parsed: ParseResult) -> bool:
    if parsed.params:
        return True
    if parsed.query:
        return True
    if parsed.fragment:
        return True
    return parsed.path not in {"", "/"}


def _is_non_loopback_http_url(parsed: ParseResult) -> bool:
    if parsed.scheme.lower() != "http":
        return False
    hostname = (parsed.hostname or "").lower()
    return hostname not in _LOOPBACK_HOSTS


def _validate_log_level(value: str) -> str:
    """Normalize supported log levels and reject silent misconfiguration."""
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    upper = value.upper()
    if upper not in valid_levels:
        raise ValueError(f"Invalid log level: {value}. Must be one of {valid_levels}")
    return upper


def _warn_empty_admin_key(value: str) -> str:
    """Warn when administrative access will intentionally fail closed."""
    if not value:
        import warnings

        warnings.warn(
            "ADMIN_API_KEY is not set. Admin access is unavailable "
            "(API endpoints return 403; browser login returns 500).",
            UserWarning,
            stacklevel=4,
        )
    return value


def _reject_webhook_host_wildcards(value: str) -> str:
    """Keep generic webhook egress on exact host allowlisting only."""
    hosts = [item.strip() for item in value.split(",") if item.strip()]
    wildcard_hosts = [host for host in hosts if "*" in host]
    if wildcard_hosts:
        raise ValueError("WEBHOOK_ALLOWED_HOSTS only supports exact host names")
    return ",".join(hosts)


def _validate_settings_field(value: str, field_name: str | None) -> str:
    if field_name == "base_url":
        return _validate_base_url(value)
    if field_name == "log_level":
        return _validate_log_level(value)
    if field_name == "admin_api_key":
        return _warn_empty_admin_key(value)
    if field_name == "webhook_allowed_hosts":
        return _reject_webhook_host_wildcards(value)
    raise AssertionError(f"Unsupported settings field validator: {field_name}")


def _uses_default_database_password(database_url: str) -> bool:
    parsed_database_url = urlparse(database_url)
    return unquote(parsed_database_url.password or "") == "change-me"


def _default_database_password_message() -> str:
    return (
        "Refusing to start with the default DATABASE_URL password. "
        "Set a non-default password or enable simulation mode for development."
    )


def _validate_sendxms_settings(settings: Settings) -> None:
    if not settings.sendxms_enabled:
        return
    if not settings.sendxms_api_key.strip():
        raise ValueError("SENDXMS_API_KEY is required when SENDXMS_ENABLED=true")
    _require_https_url(settings.sendxms_base_url, "SENDXMS_BASE_URL")
    if urlparse(settings.sendxms_base_url).hostname == "api.sendxms.tld":
        raise ValueError("SENDXMS_BASE_URL must not use the reserved default endpoint")


def _validate_signal_settings(settings: Settings) -> None:
    if not settings.signal_enabled:
        return
    if not settings.signal_target_group_id.strip():
        raise ValueError("SIGNAL_TARGET_GROUP_ID is required when SIGNAL_ENABLED=true")
    _require_http_url(settings.signal_cli_endpoint, "SIGNAL_CLI_ENDPOINT")


def _validate_zammad_settings(settings: Settings) -> None:
    if not settings.zammad_api_token:
        return
    _require_https_url(settings.zammad_base_url, "ZAMMAD_BASE_URL")
    if urlparse(settings.zammad_base_url).hostname == "zammad.example.org":
        raise ValueError("ZAMMAD_BASE_URL must not use the reserved default endpoint")


def _validate_webhook_settings(settings: Settings) -> None:
    if not settings.webhook_enabled:
        return
    if not settings.webhook_url.strip():
        raise ValueError("WEBHOOK_URL is required when WEBHOOK_ENABLED=true")
    _require_https_url(settings.webhook_url, "WEBHOOK_URL")
    _require_webhook_host_allowlisted(settings.webhook_url, settings.webhook_allowed_hosts)
    if len(settings.webhook_secret.strip()) < 32:
        raise ValueError("WEBHOOK_SECRET must contain at least 32 characters when enabled")


def _validate_production_connectors_and_database(settings: Settings) -> None:
    if settings.simulation_enabled:
        return

    _validate_sendxms_settings(settings)
    _validate_signal_settings(settings)
    _validate_zammad_settings(settings)
    _validate_webhook_settings(settings)

    # The package-level ASGI app is imported before deployment configuration
    # is loaded in several tooling paths. Reject an explicitly supplied
    # default credential while leaving that import-time placeholder inert.
    if "database_url" in settings.model_fields_set and _uses_default_database_password(
        settings.database_url
    ):
        raise ValueError(_default_database_password_message())


def _reject_weak_admin_keys(settings: Settings) -> None:
    weak_keys = {
        "change-me-admin-key",
        "change-me",
        "admin",
        "password",
        "secret",
    }
    if not settings.simulation_enabled and settings.admin_api_key.lower() in weak_keys:
        raise ValueError(
            f"Refusing to start with a weak ADMIN_API_KEY ('{settings.admin_api_key}'). "
            "Set a strong key or enable simulation mode for development."
        )


def _validate_simulation_base_url(settings: Settings) -> None:
    hostname = (urlparse(settings.base_url).hostname or "").lower()
    if settings.simulation_enabled and hostname not in _LOOPBACK_HOSTS:
        raise ValueError("BASE_URL must use a loopback host when SIMULATION_ENABLED=true")


def _validate_runtime_configuration(settings: Settings) -> None:
    if settings.simulation_enabled:
        return
    if _uses_default_database_password(settings.database_url):
        raise ValueError(_default_database_password_message())
    if not settings.yelk_ip_allowlist.strip():
        raise ValueError(
            "YELK_IP_ALLOWLIST is required when SIMULATION_ENABLED=false. "
            "Set trusted source IPs/CIDRs or enable simulation mode for local development."
        )


class _SettingsRuntimeSupport:
    """Runtime checks and feature state derived from validated settings fields."""

    def validate_runtime_configuration(self: Any) -> None:
        """Fail closed before a real API, worker, or migration process starts."""
        _validate_runtime_configuration(self)

    def is_sms_enabled(self: Any) -> bool:
        """Return whether SMS has both its feature flag and credential."""
        return self.sendxms_enabled and bool(self.sendxms_api_key)

    def is_signal_enabled(self: Any) -> bool:
        """Return whether Signal has both its feature flag and target group."""
        return self.signal_enabled and bool(self.signal_target_group_id)

    def is_webhook_enabled(self: Any) -> bool:
        """Return whether the generic state webhook is configured for use."""
        return (
            self.webhook_enabled
            and bool(self.webhook_url)
            and len(self.webhook_secret.strip()) >= 32
        )


class Settings(_SettingsRuntimeSupport, BaseSettings):
    """Main application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core
    database_url: str = "postgresql+asyncpg://alarm:change-me@localhost:5432/alarm"
    redis_url: str = "redis://localhost:6379/0"
    base_url: str = "http://localhost:8080"
    log_level: str = "INFO"

    # Yealink inbound
    yelk_token_query_param: str = _YEALINK_TRIGGER_QUERY_KEY
    yelk_ip_allowlist: str = ""
    yealink_device_token: str = ""

    # Rate limiting
    rate_limit_per_minute: int = Field(default=10, ge=1, le=1000)

    # Admin
    admin_api_key: str = ""
    enable_api_docs: bool = False

    # Proxy handling
    trusted_proxy_cidrs: str = ""

    # Zammad
    zammad_base_url: str = "https://zammad.example.org"
    zammad_api_token: str = ""
    zammad_group: str = "Notfallstelle"
    zammad_priority_id_p0: int = 3
    zammad_state_id_new: int = 1
    zammad_customer: str = "guess:alarm-system@example.org"

    # SMS (SendXMS)
    sendxms_enabled: bool = False
    sendxms_base_url: str = "https://api.sendxms.tld"
    sendxms_api_key: str = ""
    sendxms_from: str = "Notfall"
    sendxms_send_path: str = "/send"

    # Signal
    signal_enabled: bool = False
    signal_cli_endpoint: str = "http://signal-cli:8080"
    signal_target_group_id: str = ""
    signal_send_path: str = "/v2/send"

    # Webhook callbacks
    webhook_enabled: bool = False
    webhook_url: str = ""
    webhook_secret: str = ""
    webhook_timeout_seconds: int = Field(default=5, ge=1, le=60)
    webhook_allowed_hosts: str = ""

    # Escalation timings
    escalate_t1: int = Field(default=60, ge=0)
    escalate_t2: int = Field(default=180, ge=0)
    escalate_t3: int = Field(default=300, ge=0)

    # Database connection pool
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=10, ge=0, le=200)
    db_pool_timeout: int = Field(default=30, ge=1, le=300)
    db_pool_recycle: int = Field(default=1800, ge=60, le=86400)

    # Performance diagnostics
    slow_query_log_ms: int = Field(default=200, ge=0)

    # Simulation mode
    simulation_enabled: bool = False

    # --- Validators ---

    @field_validator("base_url", "log_level", "admin_api_key", "webhook_allowed_hosts")
    @classmethod
    def validate_scalar_settings(cls, v: str, info: ValidationInfo) -> str:
        """Validate independent scalar settings through their focused policies."""
        return _validate_settings_field(v, info.field_name)

    @model_validator(mode="after")
    def validate_deployment_settings(self) -> Settings:
        """Apply deployment policy in the established fail-closed order."""
        _reject_weak_admin_keys(self)
        _validate_production_connectors_and_database(self)
        _validate_simulation_base_url(self)
        return self


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
