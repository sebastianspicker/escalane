"""Reusable browser-style admin test interactions."""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from httpx import AsyncClient, Response

from tests.api_test_helpers import app_client


def hidden_input(page: str, name: str) -> str:
    """Read and decode a named hidden input from an admin HTML page."""
    match = re.search(rf'name="{name}"\s+value="([^"]*)"', page)
    assert match is not None
    return html.unescape(match.group(1))


def csrf_token(page: str) -> str:
    """Read the CSRF token emitted by an admin page."""
    return hidden_input(page, "csrf_token")


async def login_admin(
    client: AsyncClient,
    admin_key: str,
    operator_name: str,
    *,
    path: str = "/admin/login",
) -> Response:
    """Submit an admin login while allowing callers to assert the exact response."""
    return await client.post(
        path,
        data={"admin_key": admin_key, "operator_name": operator_name},
        follow_redirects=False,
    )


@asynccontextmanager
async def logged_in_admin_client(
    *, settings: object, engine: object, redis: object, admin_key: str, operator_name: str
) -> AsyncIterator[AsyncClient]:
    """Yield a running application client with an authenticated admin session."""
    async with app_client(settings=settings, engine=engine, redis=redis) as client:
        assert (await login_admin(client, admin_key, operator_name)).status_code == 303
        yield client
