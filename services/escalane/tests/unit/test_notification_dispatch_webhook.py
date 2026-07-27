"""Webhook-delivery behavior tests for NotificationService."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from escalane.core.url_validation import RetryableSSRFError

try:
    from tests.assertions import expect
    from tests.constants import value_for_test
    from tests.notification_dispatch_helpers import (
        _delivery_context,
        _make_alarm,
        _make_enriched,
        _make_settings,
        _make_svc,
        _make_target,
        _noop_session,
    )
except ModuleNotFoundError:
    from assertions import expect
    from constants import value_for_test
    from notification_dispatch_helpers import (
        _delivery_context,
        _make_alarm,
        _make_enriched,
        _make_settings,
        _make_svc,
        _make_target,
        _noop_session,
    )

pytestmark = [pytest.mark.unit]


# ── _send_webhook_notifications ───────────────────────────────────────


async def test_send_webhook_no_url_logs_error():
    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(channel="webhook", address="")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    await svc._send_webhook_notifications(session, target, payload, _make_settings())

    session.commit.assert_called()


async def test_send_webhook_ssrf_blocked():
    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(channel="webhook", address="http://169.254.169.254/metadata")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    from escalane.core.url_validation import SSRFError

    with patch(
        "escalane.services.notification_service.validate_url_not_internal",
        new_callable=AsyncMock,
        side_effect=SSRFError("SSRF blocked"),
    ):
        await svc._send_webhook_notifications(
            session,
            target,
            payload,
            _make_settings(webhook_allowed_hosts="169.254.169.254"),
        )

    session.commit.assert_called()


async def test_send_webhook_dns_failure_is_retryable_then_recovers():
    """A resolver outage is not recorded as a permanent SSRF-policy skip."""
    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(channel="webhook", address="https://hooks.example.test/hook")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    resolver = AsyncMock(
        side_effect=[RetryableSSRFError("temporary resolver failure"), ("1.1.1.1",)]
    )
    with (
        patch(
            "escalane.services.notification_service.validate_url_not_internal",
            resolver,
        ),
        patch(
            "escalane.services.notification_service._post_webhook_to_validated_addresses",
            new_callable=AsyncMock,
        ) as post,
        patch.object(svc, "_log_notification_result", new_callable=AsyncMock) as log_result,
    ):
        first_result = await svc._send_webhook_notifications(
            session,
            target,
            payload,
            _make_settings(webhook_allowed_hosts="hooks.example.test"),
        )
        second_result = await svc._send_webhook_notifications(
            session,
            target,
            payload,
            _make_settings(webhook_allowed_hosts="hooks.example.test"),
        )

    expect(first_result is False)
    expect(second_result is True)
    expect(log_result.await_args_list[0].args[3] == "error")
    expect(log_result.await_args_list[1].args[3] == "ok")
    post.assert_awaited_once()


async def test_send_webhook_empty_allowlist_rejects_without_network():
    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(channel="webhook", address="https://hooks.example.test/hook")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    with patch(
        "escalane.services.webhook_delivery.httpx.AsyncClient",
        side_effect=AssertionError("network egress must not happen"),
    ):
        await svc._send_webhook_notifications(session, target, payload, _make_settings())

    session.commit.assert_called()


async def test_send_webhook_http_error():
    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(channel="webhook", address="http://valid-external.example.com/hook")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    with patch(
        "escalane.services.notification_service.validate_url_not_internal",
        new_callable=AsyncMock,
        return_value=("1.1.1.1",),
    ):
        with patch(
            "escalane.services.webhook_delivery.httpx.AsyncClient",
            side_effect=OSError("network error"),
        ):
            await svc._send_webhook_notifications(
                session,
                target,
                payload,
                _make_settings(webhook_allowed_hosts="valid-external.example.com"),
            )

    session.commit.assert_called()


# ── _send_webhook_notifications: success path ─────────────────────────


async def test_send_webhook_success_logs_ok():
    """When the HTTP POST succeeds, _log_notification_result is called with 'ok'."""
    import httpx

    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(channel="webhook", address="http://valid-external.example.com/hook")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch(
        "escalane.services.notification_service.validate_url_not_internal",
        new_callable=AsyncMock,
        return_value=("1.1.1.1",),
    ):
        with patch(
            "escalane.services.webhook_delivery.httpx.AsyncClient",
            return_value=mock_client,
        ):
            with patch.object(svc, "_log_notification_result", new_callable=AsyncMock) as mock_log:
                await svc._send_webhook_notifications(
                    session,
                    target,
                    payload,
                    _make_settings(webhook_allowed_hosts="valid-external.example.com"),
                )

    mock_log.assert_called_once_with(session, target, payload, "ok")
    expect("X-Alarm-Delivery-ID" in mock_client.post.await_args.kwargs["headers"])


async def test_send_webhook_fails_over_to_second_validated_address():
    import httpx

    svc, session, target, payload = await _delivery_context(
        "webhook", address="https://hooks.example.test/hook"
    )
    attempts: list[tuple[str, dict[str, str], dict[str, Any]]] = []

    class FailoverClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, headers, extensions, **_kwargs):
            attempts.append((url, headers, extensions))
            if "1.1.1.1" in url:
                raise httpx.ConnectError("first address unavailable")
            return MagicMock(raise_for_status=MagicMock())

    with patch(
        "escalane.services.notification_service.validate_url_not_internal",
        new_callable=AsyncMock,
        return_value=("1.1.1.1", "8.8.8.8"),
    ):
        with patch(
            "escalane.services.webhook_delivery.httpx.AsyncClient",
            return_value=FailoverClient(),
        ):
            with patch.object(svc, "_log_notification_result", new_callable=AsyncMock) as mock_log:
                await svc._send_webhook_notifications(
                    session,
                    target,
                    payload,
                    _make_settings(webhook_allowed_hosts="hooks.example.test"),
                )

    expect([attempt[0] for attempt in attempts] == ["https://1.1.1.1/hook", "https://8.8.8.8/hook"])
    expect(all(attempt[1]["Host"] == "hooks.example.test" for attempt in attempts))
    expect(all(attempt[2]["sni_hostname"] == "hooks.example.test" for attempt in attempts))
    mock_log.assert_called_once_with(session, target, payload, "ok")


async def test_send_webhook_stops_on_permanent_response() -> None:
    import httpx

    svc, session, target, payload = await _delivery_context(
        "webhook", address="https://hooks.example.test/hook"
    )
    attempts: list[str] = []
    request = httpx.Request("POST", "https://1.1.1.1/hook")
    response = MagicMock()
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "invalid request", request=request, response=httpx.Response(400, request=request)
    )

    class PermanentFailureClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, **_kwargs):
            attempts.append(url)
            return response

    with (
        patch(
            "escalane.services.notification_service.validate_url_not_internal",
            new_callable=AsyncMock,
            return_value=("1.1.1.1", "8.8.8.8"),
        ),
        patch(
            "escalane.services.webhook_delivery.httpx.AsyncClient",
            return_value=PermanentFailureClient(),
        ),
        patch.object(svc, "_log_notification_result", new_callable=AsyncMock) as log_result,
    ):
        delivered = await svc._send_webhook_notifications(
            session,
            target,
            payload,
            _make_settings(webhook_allowed_hosts="hooks.example.test"),
        )

    expect(delivered is True)
    expect(attempts == ["https://1.1.1.1/hook"])
    expect(log_result.await_args.args[3] == "error")


async def test_send_webhook_logs_one_safe_error_when_all_validated_addresses_fail():
    import httpx

    svc, session = _make_svc(), await _noop_session()
    secret = value_for_test("target-webhook-query")
    target = _make_target(
        channel="webhook", address=f"https://hooks.example.test/hook?token={secret}"
    )
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )
    attempts: list[str] = []

    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, **_kwargs):
            attempts.append(url)
            raise httpx.ConnectError(f"delivery failed for {url}")

    with patch(
        "escalane.services.notification_service.validate_url_not_internal",
        new_callable=AsyncMock,
        return_value=("1.1.1.1", "8.8.8.8"),
    ):
        with patch(
            "escalane.services.webhook_delivery.httpx.AsyncClient",
            return_value=FailingClient(),
        ):
            with patch.object(svc, "_log_notification_result", new_callable=AsyncMock) as mock_log:
                await svc._send_webhook_notifications(
                    session,
                    target,
                    payload,
                    _make_settings(webhook_allowed_hosts="hooks.example.test"),
                )

    expect(
        attempts
        == [
            f"https://1.1.1.1/hook?token={secret}",
            f"https://8.8.8.8/hook?token={secret}",
        ]
    )
    error = mock_log.await_args.args[4]
    expect(mock_log.await_args.args[3] == "error")
    expect(secret not in error)
