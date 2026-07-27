"""Database-side JSON object updates that preserve unrelated keys."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from typing import cast as type_cast

from sqlalchemy import JSON, cast, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.elements import ColumnElement


def merge_json_object(
    column: Any,
    patch: Mapping[Any, Any],
    *,
    dialect_name: str,
) -> ColumnElement[Any]:
    """Merge top-level keys without a stale read/modify/write round trip.

    PostgreSQL evaluates the JSONB merge after acquiring the row's update lock.
    SQLite's ``json_set`` has the same top-level replacement semantics for tests
    and local development while preserving explicit JSON ``null`` values.
    """
    if dialect_name == "postgresql":
        # Let JSONB's bind processor serialize the mapping exactly once. Passing
        # pre-serialized JSON here turns it into a JSON string, and PostgreSQL's
        # object || scalar semantics then produce an array instead of an object.
        merged = cast(column, JSONB).op("||")(cast(dict(patch), JSONB))
        return type_cast(ColumnElement[Any], cast(merged, JSON))

    sqlite_merged: Any = column
    for key, value in patch.items():
        escaped_key = str(key).replace('"', '\\"')
        sqlite_merged = func.json_set(
            sqlite_merged,
            f'$."{escaped_key}"',
            func.json(json.dumps(value)),
        )
    return type_cast(ColumnElement[Any], sqlite_merged)
