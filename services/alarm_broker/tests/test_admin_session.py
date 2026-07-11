from __future__ import annotations

import pytest
from fastapi import HTTPException

from alarm_broker.api.admin_session import (
    create_admin_session,
    destroy_admin_session,
    require_admin_session,
    validate_admin_csrf,
)

try:
    from tests.helpers import FakeRedis
except ModuleNotFoundError:
    from helpers import FakeRedis


pytestmark = pytest.mark.unit


async def test_admin_session_keeps_named_context_and_expires(settings) -> None:
    settings.admin_api_key = "session-test-key"
    redis = FakeRedis()
    created = await create_admin_session(redis, settings, "Leitstelle Nord")

    loaded = await require_admin_session(redis, settings, created.token, extend=False)
    assert loaded.operator_name == "Leitstelle Nord"
    assert loaded.csrf_token == created.csrf_token

    redis.advance(3601)
    with pytest.raises(HTTPException) as exc_info:
        await require_admin_session(redis, settings, created.token, extend=False)
    assert exc_info.value.status_code == 401


async def test_admin_session_csrf_and_logout(settings) -> None:
    settings.admin_api_key = "session-test-key"
    redis = FakeRedis()
    created = await create_admin_session(redis, settings, "Operator")

    validate_admin_csrf(created, created.csrf_token)
    with pytest.raises(HTTPException) as exc_info:
        validate_admin_csrf(created, "wrong")
    assert exc_info.value.status_code == 403

    await destroy_admin_session(redis, created.token)
    with pytest.raises(HTTPException):
        await require_admin_session(redis, settings, created.token, extend=False)
