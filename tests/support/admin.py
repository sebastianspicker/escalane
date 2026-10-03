"""Browser-style admin login, CSRF, and acknowledgement-form interactions."""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from httpx import AsyncClient, Response

from tests.support.assertions import expect
from tests.support.clients import app_client


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
    admin_key: str = "dev-admin-key",
    operator_name: str | None = None,
    *,
    path: str = "/admin/login",
    require_success: bool = False,
) -> Response:
    """Submit an admin login via cookie-based auth.

    The response is returned so callers can assert its exact status. With
    ``require_success`` the login must answer 200 or 303, which sets the
    ``admin_session`` cookie on the client for subsequent requests.
    """
    data = {"admin_key": admin_key}
    if operator_name is not None:
        data["operator_name"] = operator_name
    response = await client.post(path, data=data, follow_redirects=False)
    if require_success:
        expect(
            response.status_code in (200, 303),
            f"Admin login failed with status {response.status_code}: {response.text}",
        )
    return response


@asynccontextmanager
async def logged_in_admin_client(
    *, settings: object, engine: object, redis: object, admin_key: str, operator_name: str
) -> AsyncIterator[AsyncClient]:
    """Yield a running application client with an authenticated admin session."""
    async with app_client(settings=settings, engine=engine, redis=redis) as client:
        assert (await login_admin(client, admin_key, operator_name)).status_code == 303
        yield client


async def ack_with_csrf(
    client: AsyncClient,
    ack_token: str,
    *,
    acked_by: str = "Tester",
    note: str = "",
) -> Response:
    """Submit the ACK form with proper CSRF token handling.

    1. GET the ACK page to obtain the CSRF cookie.
    2. Extract the CSRF hidden field value from the HTML.
    3. POST with both the cookie and the form field.
    """
    get_resp = await client.get(f"/a/{ack_token}")
    expect(get_resp.status_code == 200, get_resp.text)

    # Extract CSRF token from the hidden form field
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', get_resp.text)
    csrf_value = match.group(1) if match else ""

    data: dict[str, str] = {"acked_by": acked_by}
    if note:
        data["note"] = note
    if csrf_value:
        data["csrf_token"] = csrf_value

    return await client.post(f"/a/{ack_token}", data=data)
