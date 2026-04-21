"""Shared test helpers — importable from any working directory via relative import."""

from __future__ import annotations

import re
import time
import uuid

from httpx import AsyncClient, Response


async def admin_login(client: AsyncClient, admin_key: str = "dev-admin-key") -> None:
    """Log in to the admin dashboard via cookie-based auth.

    POSTs to /admin/login with the given admin key, which sets
    an ``admin_session`` cookie on the client for subsequent requests.

    In local HTTP tests, the server now emits a non-secure cookie so httpx
    forwards it naturally on subsequent requests.
    """
    resp = await client.post("/admin/login", data={"admin_key": admin_key}, follow_redirects=False)
    assert resp.status_code in (200, 303), (
        f"Admin login failed with status {resp.status_code}: {resp.text}"
    )


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
    assert get_resp.status_code == 200, get_resp.text

    # Extract CSRF token from the hidden form field
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', get_resp.text)
    csrf_value = match.group(1) if match else ""

    data: dict[str, str] = {"acked_by": acked_by}
    if note:
        data["note"] = note
    if csrf_value:
        data["csrf_token"] = csrf_value

    return await client.post(f"/a/{ack_token}", data=data)


class FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._expiries: dict[str, float] = {}
        self.jobs: list[tuple[str, tuple]] = []
        self._job_ids: set[str] = set()
        self._now = time.monotonic()

    def _purge_expired(self, key: str) -> None:
        expires_at = self._expiries.get(key)
        if expires_at is not None and expires_at <= self._now:
            self._store.pop(key, None)
            self._expiries.pop(key, None)

    def advance(self, seconds: float) -> None:
        self._now += seconds

    async def close(self) -> None:
        return None

    async def get(self, key: str) -> str | None:
        self._purge_expired(key)
        return self._store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool:
        self._purge_expired(key)
        if nx and key in self._store:
            return False
        self._store[key] = value
        if ex is not None:
            self._expiries[key] = self._now + ex
        else:
            self._expiries.pop(key, None)
        return True

    async def delete(self, key: str) -> int:
        self._purge_expired(key)
        self._expiries.pop(key, None)
        return 1 if self._store.pop(key, None) is not None else 0

    async def incr(self, key: str) -> int:
        self._purge_expired(key)
        current = int(self._store.get(key, "0")) + 1
        self._store[key] = str(current)
        return current

    async def expire(self, key: str, seconds: int) -> bool:
        self._purge_expired(key)
        if key not in self._store:
            return False
        self._expiries[key] = self._now + seconds
        return True

    async def enqueue_job(self, name: str, *args, **kwargs):
        job_id = kwargs.get("_job_id")
        if isinstance(job_id, str):
            if job_id in self._job_ids:
                return None
            self._job_ids.add(job_id)
        self.jobs.append((name, args))
        return object()


async def trigger_alarm(client: AsyncClient) -> uuid.UUID:
    """Trigger an alarm via the Yealink endpoint and return the alarm UUID."""
    response = await client.get("/v1/yealink/alarm", params={"token": "YLK_T54W_3F9A"})
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["alarm_id"])
