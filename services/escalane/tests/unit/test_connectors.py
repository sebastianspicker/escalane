"""Tests for connector implementations: Zammad, SendXMS, Signal."""

from __future__ import annotations

from contextlib import asynccontextmanager

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import httpx
import pytest
import respx

from escalane.connectors.sendxms import SendXmsClient, SendXmsConfig
from escalane.connectors.signal import SignalClient, SignalConfig
from escalane.connectors.zammad import ZammadClient, ZammadConfig

try:
    from tests.constants import EMPTY_SECRET_VALUE, TEST_SMS_KEY, TEST_ZAMMAD_TOKEN
except ModuleNotFoundError:
    from constants import EMPTY_SECRET_VALUE, TEST_SMS_KEY, TEST_ZAMMAD_TOKEN

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# a) test_zammad_create_ticket
# ---------------------------------------------------------------------------


def _zammad_config(**overrides) -> ZammadConfig:
    """Build the enabled Zammad configuration used by request-contract tests."""
    defaults = {
        "enabled": True,
        "base_url": "https://zammad.example.test",
        "api_token": TEST_ZAMMAD_TOKEN,
    }
    defaults.update(overrides)
    return ZammadConfig(**defaults)


def _signal_config(**overrides) -> SignalConfig:
    """Build the enabled Signal configuration used by request-contract tests."""
    defaults = {
        "enabled": True,
        "endpoint": "https://signal-cli.test",
        "target_group_id": "default-group-id",
        "send_path": "/v2/send",
    }
    defaults.update(overrides)
    return SignalConfig(**defaults)


@asynccontextmanager
async def _connector_client(client_type, config):
    """Provide a connector client backed by a short-lived test HTTP transport."""
    async with httpx.AsyncClient(verify=False) as http:
        yield client_type(http=http, config=config)


async def test_zammad_create_ticket():
    """Verify ZammadClient.create_ticket POSTs correct payload and returns ticket ID."""
    config = _zammad_config(
        group="Notfallstelle",
        state_id_new=1,
        customer="guess:alarm@example.org",
    )

    async with _connector_client(ZammadClient, config) as client:
        with respx.mock(assert_all_called=True) as mock_router:

            def _check_request(request: httpx.Request) -> httpx.Response:
                expect(request.headers["Authorization"] == f"Bearer {TEST_ZAMMAD_TOKEN}")
                import json

                body = json.loads(request.content)
                expect(body["title"] == "Test Alarm Ticket")
                expect(body["group"] == "Notfallstelle")
                return httpx.Response(200, json={"id": 42, "title": "Test Alarm Ticket"})

            mock_router.post("https://zammad.example.test/api/v1/tickets").mock(
                side_effect=_check_request
            )

            ticket_id = await client.create_ticket(
                {
                    "title": "Test Alarm Ticket",
                    "group": "Notfallstelle",
                    "priority_id": 3,
                    "state_id": 1,
                    "customer_id": "guess:alarm@example.org",
                    "article": {
                        "subject": "Alarm ausgelöst (silent)",
                        "body": "Test body",
                        "type": "note",
                        "internal": True,
                    },
                }
            )

    expect(ticket_id == 42)


async def test_zammad_add_internal_note():
    """Verify ZammadClient.add_internal_note PUTs correct payload."""
    config = _zammad_config()

    async with _connector_client(ZammadClient, config) as client:
        with respx.mock(assert_all_called=True) as mock_router:

            def _check_request(request: httpx.Request) -> httpx.Response:
                import json

                body = json.loads(request.content)
                expect(body["article"]["subject"] == "Alarm quittiert")
                expect(body["article"]["internal"] is True)
                return httpx.Response(200, json={"ok": True})

            mock_router.put("https://zammad.example.test/api/v1/tickets/42").mock(
                side_effect=_check_request
            )

            await client.add_internal_note(42, subject="Alarm quittiert", body="ACK durch: Tester")


# ---------------------------------------------------------------------------
# b) test_sendxms_send_sms
# ---------------------------------------------------------------------------


async def test_sendxms_send_sms():
    """Verify SendXmsClient.send_sms POSTs correct payload."""
    config = SendXmsConfig(
        enabled=True,
        base_url="https://api.sendxms.test",
        api_key=TEST_SMS_KEY,
        from_name="Notfall",
        send_path="/send",
    )

    async with _connector_client(SendXmsClient, config) as client:
        with respx.mock(assert_all_called=True) as mock_router:

            def _check_request(request: httpx.Request) -> httpx.Response:
                expect(f"Bearer {TEST_SMS_KEY}" in request.headers.get("Authorization", ""))
                import json

                body = json.loads(request.content)
                expect(body["to"] == "+491234567")
                expect(body["message"] == "NOTFALLALARM: Test")
                expect(body["from"] == "Notfall")
                return httpx.Response(200, json={"ok": True})

            mock_router.post("https://api.sendxms.test/send").mock(side_effect=_check_request)

            await client.send_sms("+491234567", "NOTFALLALARM: Test")


async def test_sendxms_disabled_noop():
    """When disabled, send_sms should return without making any HTTP calls."""
    config = SendXmsConfig(
        enabled=False,
        base_url="https://api.sendxms.test",
        api_key=TEST_SMS_KEY,
    )

    async with _connector_client(SendXmsClient, config) as client:
        with respx.mock as mock_router:
            mock_router.post("https://api.sendxms.test/send").respond(200)
            await client.send_sms("+491234567", "Should not be sent")
            # Route should NOT have been called
            expect(not mock_router.routes[0].called)


# ---------------------------------------------------------------------------
# c) test_signal_send_group_message
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "group_id", "expected_group"),
    [
        ("NOTFALLALARM: Test message", "custom-group-123", "custom-group-123"),
        ("Test message", None, "default-group-id"),
    ],
)
async def test_signal_send_group_message(message, group_id, expected_group):
    """Signal uses an explicit group ID when supplied, otherwise its configured default."""
    config = _signal_config()

    async with _connector_client(SignalClient, config) as client:
        with respx.mock(assert_all_called=True) as mock_router:

            def _check_request(request: httpx.Request) -> httpx.Response:
                import json

                body = json.loads(request.content)
                expect(body["message"] == message)
                expect(body["groupId"] == expected_group)
                return httpx.Response(200, json={"ok": True})

            mock_router.post("https://signal-cli.test/v2/send").mock(side_effect=_check_request)

            await client.send_group_message(message, group_id=group_id)


async def test_signal_disabled_noop():
    """When disabled, send_group_message should return without making HTTP calls."""
    config = SignalConfig(
        enabled=False,
        endpoint="https://signal-cli.test",
        target_group_id="default-group-id",
    )

    async with _connector_client(SignalClient, config) as client:
        with respx.mock as mock_router:
            mock_router.post("https://signal-cli.test/v2/send").respond(200)
            await client.send_group_message("Should not be sent")
            expect(not mock_router.routes[0].called)


# ---------------------------------------------------------------------------
# d) test_connector_disabled_raises
# ---------------------------------------------------------------------------


async def test_connector_disabled_raises():
    """BaseConnector._request raises RuntimeError when disabled."""
    from escalane.connectors.base import BaseConnector, BaseConnectorConfig

    config = BaseConnectorConfig(enabled=False, base_url="https://example.test")

    async with httpx.AsyncClient(verify=False) as http:
        connector = BaseConnector(http, config)

        with pytest.raises(RuntimeError, match="not enabled"):
            await connector._request("POST", "/test", json={"a": 1})


async def test_zammad_disabled_not_enabled():
    """ZammadClient.enabled() returns False when api_token is empty."""
    config = ZammadConfig(
        enabled=True,
        base_url="https://zammad.example.test",
        api_token=EMPTY_SECRET_VALUE,  # empty -> disabled
    )

    async with httpx.AsyncClient(verify=False) as http:
        client = ZammadClient(http=http, config=config)
        expect(client.enabled() is False)
