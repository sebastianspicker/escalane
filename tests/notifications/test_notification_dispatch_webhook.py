"""Webhook-delivery behavior tests for NotificationService."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from escalane.providers import webhook as webhook_delivery
from escalane.security.url_validation import RetryableSSRFError
from tests.support.assertions import expect
from tests.support.constants import value_for_test
from tests.support.notification_dispatch_helpers import (
    _delivery_context,
    _make_alarm,
    _make_enriched,
    _make_settings,
    _make_svc,
    _make_target,
    _noop_session,
)

pytestmark = [pytest.mark.unit]


async def test_webhook_transport_shares_one_client_and_timeout_across_failover() -> None:
    """Validated-address failover stays inside one client and timeout budget."""
    _svc, _session, _target, payload = await _delivery_context("webhook")
    attempts: list[str] = []

    class OverallTimeout:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_args: object) -> bool:
            return False

    class FailoverClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def post(self, url: str, **_kwargs: object) -> MagicMock:
            attempts.append(url)
            if len(attempts) == 1:
                raise httpx.ConnectError("first address unavailable")
            return MagicMock(raise_for_status=MagicMock())

    timeout_factory = MagicMock(return_value=OverallTimeout())
    client_factory = MagicMock(return_value=FailoverClient())
    with (
        patch("escalane.providers.webhook.asyncio.timeout", timeout_factory),
        patch("escalane.providers.webhook.httpx.AsyncClient", client_factory),
    ):
        await webhook_delivery.post_webhook_to_validated_addresses(
            "https://hooks.example.test/hook",
            payload,
            ("1.1.1.1", "8.8.8.8"),
            "target-id",
            "delivery-id",
            30.0,
        )

    timeout_factory.assert_called_once_with(30.0)
    client_factory.assert_called_once_with(timeout=30.0, trust_env=False)
    expect(attempts == ["https://1.1.1.1/hook", "https://8.8.8.8/hook"])


async def test_webhook_transport_preserves_final_retryable_error_and_safe_logs(caplog) -> None:
    """Every failed pinned address logs redacted diagnostics before final propagation."""
    _svc, _session, _target, payload = await _delivery_context("webhook")
    secret = value_for_test("transport-log-secret")
    username = "webhook-user"
    failures: list[httpx.ConnectError] = []

    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def post(self, url: str, **_kwargs: object) -> None:
            error = httpx.ConnectError(f"delivery failed for {url}")
            failures.append(error)
            raise error

    caplog.set_level(logging.WARNING, logger="escalane")
    with patch("escalane.providers.webhook.httpx.AsyncClient", return_value=FailingClient()):
        with pytest.raises(httpx.ConnectError) as raised:
            await webhook_delivery.post_webhook_to_validated_addresses(
                f"https://{username}:{secret}@hooks.example.test/private/{secret}?token={secret}",
                payload,
                ("1.1.1.1", "8.8.8.8"),
                "target-id",
                "delivery-id",
                30.0,
            )

    expect(raised.value is failures[-1])
    records = [
        record
        for record in caplog.records
        if record.message == "webhook_notification_address_failed"
    ]
    expect(len(records) == 2)
    for record in records:
        expect(record.target_id == "target-id")
        expect(record.url == "https://hooks.example.test")
        expect(record.error == "Downstream provider transport error")
        expect(secret not in record.getMessage())
        expect(secret not in record.url)
        expect(secret not in record.error)
        expect(username not in record.getMessage())
        expect(username not in record.url)
        expect(username not in record.error)


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

    from escalane.security.url_validation import SSRFError

    with patch(
        "escalane.notifications.dispatch.validate_url_not_internal",
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
            "escalane.notifications.dispatch.validate_url_not_internal",
            resolver,
        ),
        patch(
            "escalane.notifications.dispatch.post_webhook_to_validated_addresses",
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
        "escalane.providers.webhook.httpx.AsyncClient",
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
        "escalane.notifications.dispatch.validate_url_not_internal",
        new_callable=AsyncMock,
        return_value=("1.1.1.1",),
    ):
        with patch(
            "escalane.providers.webhook.httpx.AsyncClient",
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
        "escalane.notifications.dispatch.validate_url_not_internal",
        new_callable=AsyncMock,
        return_value=("1.1.1.1",),
    ):
        with patch(
            "escalane.providers.webhook.httpx.AsyncClient",
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
        "escalane.notifications.dispatch.validate_url_not_internal",
        new_callable=AsyncMock,
        return_value=("1.1.1.1", "8.8.8.8"),
    ):
        with patch(
            "escalane.providers.webhook.httpx.AsyncClient",
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
            "escalane.notifications.dispatch.validate_url_not_internal",
            new_callable=AsyncMock,
            return_value=("1.1.1.1", "8.8.8.8"),
        ),
        patch(
            "escalane.providers.webhook.httpx.AsyncClient",
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
        "escalane.notifications.dispatch.validate_url_not_internal",
        new_callable=AsyncMock,
        return_value=("1.1.1.1", "8.8.8.8"),
    ):
        with patch(
            "escalane.providers.webhook.httpx.AsyncClient",
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
