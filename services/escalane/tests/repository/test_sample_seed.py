"""Regression test for the sample seed data documented in public quickstarts."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from escalane.settings import Settings
from tests.constants import TEST_ADMIN_API_KEY, TEST_DEVICE_TOKEN

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.repository


@pytest.mark.anyio
async def test_documented_sample_seed_returns_http_200(app, settings: Settings) -> None:
    settings.yealink_device_token = TEST_DEVICE_TOKEN
    settings.signal_target_group_id = "sample-signal-group"

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://localhost",
        ) as client:
            response = await client.post(
                "/v1/admin/seed",
                headers={
                    "X-Admin-Key": TEST_ADMIN_API_KEY,
                    "Content-Type": "application/x-yaml",
                },
                content=(REPOSITORY_ROOT / "deploy/seed.example.yaml").read_bytes(),
            )

    assert response.status_code == 200
    assert response.json() == {"ok": "true"}
