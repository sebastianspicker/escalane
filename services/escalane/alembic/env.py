"""Configure Alembic to run synchronous migrations against application metadata."""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from escalane.db import models as _models  # noqa: F401
from escalane.db.base import Base
from escalane.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_db_url() -> str:
    """Convert async application URLs to drivers Alembic can use synchronously."""
    url = os.getenv("DATABASE_URL") or get_settings().database_url
    url = str(url)
    _DRIVER_MAP = {
        "postgresql+asyncpg://": "postgresql+psycopg://",
        "postgresql+psycopg2://": "postgresql+psycopg://",
    }
    for async_prefix, sync_prefix in _DRIVER_MAP.items():
        if url.startswith(async_prefix):
            return url.replace(async_prefix, sync_prefix, 1)
    return url


def run_migrations_offline() -> None:
    """Render migrations without a database connection for review or tooling."""
    url = _sync_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Validate runtime settings and apply migrations through a live connection."""
    get_settings().validate_runtime_configuration()
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _sync_db_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
