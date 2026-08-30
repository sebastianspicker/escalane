"""Provide the shared declarative base used by every durable ORM model."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Anchor SQLAlchemy metadata so migrations and models share one schema registry."""

    pass
