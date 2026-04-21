"""Tests for connector implementations: Zammad, SendXMS, Signal."""

from __future__ import annotations

import httpx
import pytest
import respx

from alarm_broker.connectors.sendxms import SendXmsClient, SendXmsConfig
from alarm_broker.connectors.signal import SignalClient, SignalConfig
from alarm_broker.connectors.zammad import ZammadClient, ZammadConfig

# ---------------------------------------------------------------------------
# a) test_zammad_create_ticket
# ---------------------------------------------------------------------------


async def test_zammad_create_ticket():
    """Verify ZammadClient.create_ticket POSTs correct payload and returns ticket ID."""
    config = ZammadConfig(
        enabled=True,
        base_url="https://zammad.example.test",
        api_token="test-token-123",
        group="Notfallstelle",
        state_id_new=1,
        customer="guess:alarm@example.org",
    )

    async with httpx.AsyncClient() as http:
        client = ZammadClient(http=http, config=config)

        with respx.mock(assert_all_called=True) as mock_router:

            def _check_request(request: httpx.Request) -> httpx.Response:
                assert request.headers["Authorization"] == "Bearer test-token-123"
                import json

                body = json.loads(request.content)
                assert body["title"] == "Test Alarm Ticket"
                assert body["group"] == "Notfallstelle"
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

    assert ticket_id == 42


async def test_zammad_add_internal_note():
    """Verify ZammadClient.add_internal_note PUTs correct payload."""
    config = ZammadConfig(
        enabled=True,
        base_url="https://zammad.example.test",
        api_token="test-token-123",
    )

    async with httpx.AsyncClient() as http:
        client = ZammadClient(http=http, config=config)

        with respx.mock(assert_all_called=True) as mock_router:

            def _check_request(request: httpx.Request) -> httpx.Response:
                import json

                body = json.loads(request.content)
                assert body["article"]["subject"] == "Alarm quittiert"
                assert body["article"]["internal"] is True
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
        api_key="sms-api-key",
        from_name="Notfall",
        send_path="/send",
    )

    async with httpx.AsyncClient() as http:
        client = SendXmsClient(http=http, config=config)

        with respx.mock(assert_all_called=True) as mock_router:

            def _check_request(request: httpx.Request) -> httpx.Response:
                assert "Bearer sms-api-key" in request.headers.get("Authorization", "")
                import json

                body = json.loads(request.content)
                assert body["to"] == "+491234567"
                assert body["message"] == "NOTFALLALARM: Test"
                assert body["from"] == "Notfall"
                return httpx.Response(200, json={"ok": True})

            mock_router.post("https://api.sendxms.test/send").mock(side_effect=_check_request)

            await client.send_sms("+491234567", "NOTFALLALARM: Test")


async def test_sendxms_disabled_noop():
    """When disabled, send_sms should return without making any HTTP calls."""
    config = SendXmsConfig(
        enabled=False,
        base_url="https://api.sendxms.test",
        api_key="sms-api-key",
    )

    async with httpx.AsyncClient() as http:
        client = SendXmsClient(http=http, config=config)

        with respx.mock as mock_router:
            mock_router.post("https://api.sendxms.test/send").respond(200)
            await client.send_sms("+491234567", "Should not be sent")
            # Route should NOT have been called
            assert not mock_router.routes[0].called


# ---------------------------------------------------------------------------
# c) test_signal_send_group_message
# ---------------------------------------------------------------------------


async def test_signal_send_group_message():
    """Verify SignalClient.send_group_message POSTs correct payload."""
    config = SignalConfig(
        enabled=True,
        endpoint="https://signal-cli.test",
        target_group_id="default-group-id",
        send_path="/v2/send",
    )

    async with httpx.AsyncClient() as http:
        client = SignalClient(http=http, config=config)

        with respx.mock(assert_all_called=True) as mock_router:

            def _check_request(request: httpx.Request) -> httpx.Response:
                import json

                body = json.loads(request.content)
                assert body["message"] == "NOTFALLALARM: Test message"
                assert body["groupId"] == "custom-group-123"
                return httpx.Response(200, json={"ok": True})

            mock_router.post("https://signal-cli.test/v2/send").mock(side_effect=_check_request)

            await client.send_group_message(
                "NOTFALLALARM: Test message", group_id="custom-group-123"
            )


async def test_signal_uses_default_group_id():
    """When no group_id is passed, the default target_group_id should be used."""
    config = SignalConfig(
        enabled=True,
        endpoint="https://signal-cli.test",
        target_group_id="default-group-id",
        send_path="/v2/send",
    )

    async with httpx.AsyncClient() as http:
        client = SignalClient(http=http, config=config)

        with respx.mock(assert_all_called=True) as mock_router:

            def _check_request(request: httpx.Request) -> httpx.Response:
                import json

                body = json.loads(request.content)
                assert body["groupId"] == "default-group-id"
                return httpx.Response(200, json={"ok": True})

            mock_router.post("https://signal-cli.test/v2/send").mock(side_effect=_check_request)

            await client.send_group_message("Test message")


async def test_signal_disabled_noop():
    """When disabled, send_group_message should return without making HTTP calls."""
    config = SignalConfig(
        enabled=False,
        endpoint="https://signal-cli.test",
        target_group_id="default-group-id",
    )

    async with httpx.AsyncClient() as http:
        client = SignalClient(http=http, config=config)

        with respx.mock as mock_router:
            mock_router.post("https://signal-cli.test/v2/send").respond(200)
            await client.send_group_message("Should not be sent")
            assert not mock_router.routes[0].called


# ---------------------------------------------------------------------------
# d) test_connector_disabled_raises
# ---------------------------------------------------------------------------


async def test_connector_disabled_raises():
    """BaseConnector._request_with_retry raises RuntimeError when disabled."""
    from alarm_broker.connectors.base import BaseConnector, BaseConnectorConfig

    config = BaseConnectorConfig(enabled=False, base_url="https://example.test")

    async with httpx.AsyncClient() as http:
        connector = BaseConnector(http, config)

        with pytest.raises(RuntimeError, match="not enabled"):
            await connector._request_with_retry("POST", "/test", json={"a": 1})


async def test_zammad_disabled_not_enabled():
    """ZammadClient.enabled() returns False when api_token is empty."""
    config = ZammadConfig(
        enabled=True,
        base_url="https://zammad.example.test",
        api_token="",  # empty -> disabled
    )

    async with httpx.AsyncClient() as http:
        client = ZammadClient(http=http, config=config)
        assert client.enabled() is False
