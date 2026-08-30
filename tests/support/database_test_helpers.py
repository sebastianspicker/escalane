"""Shared database initialization for in-process and served API tests."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from escalane.persistence.base import Base
from escalane.web.routes.health import EXPECTED_ALEMBIC_HEAD


async def initialized_sqlite_engine(db_path: Path) -> AsyncEngine:
    """Create a SQLite engine with ORM tables and the production migration marker."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        await connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:version)"),
            {"version": EXPECTED_ALEMBIC_HEAD},
        )
    return engine
