"""Notification audit and retry-classification tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from escalane.notifications.delivery import NotificationDeliveryError, log_notification
from tests.support.assertions import expect
from tests.support.notification_dispatch_helpers import (
    _ALARM_ID,
    _NOW,
    _make_alarm,
    _make_enriched,
    _make_svc,
    _make_target,
    _noop_session,
)

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize(
    ("status_code", "expected_delivered"),
    [(400, True), (503, False)],
    ids=["permanent-no-retry", "transient-retry"],
)
async def test_provider_failure_retry_classification(
    status_code: int, expected_delivered: bool
) -> None:
    import httpx

    svc, session = _make_svc(), await _noop_session()
    target = _make_target(channel="signal", address="group-id")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )
    request = httpx.Request("POST", "https://signal.example.test/send")
    svc._signal.send_group_message = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "provider failure",
            request=request,
            response=httpx.Response(status_code, request=request),
        )
    )

    with patch.object(svc, "_log_notification_result", new_callable=AsyncMock) as log_result:
        delivered = await svc._send_via_signal(session, target, payload["body"], payload)

    expect(delivered is expected_delivered)
    expect(log_result.await_args.args[3] == "error")


# ── handle_zammad_ticket ───────────────────────────────────────────────


async def test_handle_zammad_ticket_disabled_returns_none():
    svc = _make_svc(zammad_enabled=False)
    session = await _noop_session()

    result = await svc.handle_zammad_ticket(
        session,
        alarm=_make_alarm(),
        enriched=_make_enriched(),
        ack_url=None,
    )

    expect(result is None)


async def test_handle_zammad_ticket_create_exception_requests_worker_retry():
    import httpx

    svc = _make_svc(zammad_enabled=True)
    request = httpx.Request("POST", "https://zammad.example.test/api/v1/tickets")
    svc._zammad.create_ticket = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "unavailable", request=request, response=httpx.Response(503, request=request)
        )
    )
    session = await _noop_session()

    with pytest.raises(NotificationDeliveryError, match="ticket creation"):
        await svc.handle_zammad_ticket(
            session,
            alarm=_make_alarm(),
            enriched=_make_enriched(),
            ack_url=None,
        )


async def test_handle_zammad_ticket_permanent_failure_is_audited_without_retry():
    import httpx

    svc = _make_svc(zammad_enabled=True)
    request = httpx.Request("POST", "https://zammad.example.test/api/v1/tickets")
    svc._zammad.create_ticket = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "invalid request", request=request, response=httpx.Response(400, request=request)
        )
    )
    session = await _noop_session()

    result = await svc.handle_zammad_ticket(
        session,
        alarm=_make_alarm(),
        enriched=_make_enriched(),
        ack_url=None,
    )

    expect(result is None)
    session.commit.assert_awaited_once()


async def test_handle_zammad_ticket_success_returns_ticket_id():
    svc = _make_svc(zammad_enabled=True)
    svc._zammad.create_ticket = AsyncMock(return_value=42)
    session = await _noop_session()

    result = await svc.handle_zammad_ticket(
        session,
        alarm=_make_alarm(),
        enriched=_make_enriched(),
        ack_url="http://x/a/tok",
    )

    expect(result == 42)


async def test_successful_connector_audit_failure_propagates_for_worker_retry():
    """A failed success audit must not be mistaken for a provider failure or swallowed."""
    svc = _make_svc()
    session = await _noop_session()
    session.commit.side_effect = RuntimeError("database unavailable")
    session.rollback = AsyncMock()
    target = _make_target(channel="signal", address="group-1")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    with pytest.raises(NotificationDeliveryError, match="audit persistence"):
        await svc._send_via_signal(session, target, payload["body"], payload)

    svc._signal.send_group_message.assert_awaited_once()
    session.rollback.assert_awaited_once()


# ── add_zammad_ack_note ────────────────────────────────────────────────


async def test_add_zammad_ack_note_disabled_returns_false():
    svc = _make_svc(zammad_enabled=False)
    session = await _noop_session()

    result = await svc.add_zammad_ack_note(
        session,
        alarm_id=_ALARM_ID,
        ticket_id=10,
        acked_by="user",
        acked_at=_NOW,
        note=None,
    )

    expect(result is False)


async def test_add_zammad_ack_note_permanent_exception_is_complete_without_retry():
    svc = _make_svc(zammad_enabled=True)
    svc._zammad.add_internal_note = AsyncMock(side_effect=RuntimeError("zammad error"))
    session = await _noop_session()

    result = await svc.add_zammad_ack_note(
        session,
        alarm_id=_ALARM_ID,
        ticket_id=10,
        acked_by="user",
        acked_at=_NOW,
        note="note text",
    )

    expect(result is True)
    expect(session.add.call_args.args[0].result == "permanent_error")


async def test_add_zammad_ack_note_transient_exception_requests_retry():
    import httpx

    svc = _make_svc(zammad_enabled=True)
    request = httpx.Request("PUT", "https://zammad.example.test/api/v1/tickets/10")
    svc._zammad.add_internal_note = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "unavailable", request=request, response=httpx.Response(503, request=request)
        )
    )
    session = await _noop_session()

    result = await svc.add_zammad_ack_note(
        session,
        alarm_id=_ALARM_ID,
        ticket_id=10,
        acked_by="user",
        acked_at=_NOW,
        note="note text",
    )

    expect(result is False)
    expect(session.add.call_args.args[0].result == "error")


# ── log_notification (module-level) ───────────────────────────────────


async def test_log_notification_module_fn():
    session = await _noop_session()

    await log_notification(
        session,
        alarm_id=_ALARM_ID,
        channel="sms",
        target_id="t1",
        payload={"msg": "test"},
        result="ok",
    )

    session.add.assert_called_once()
    session.commit.assert_called_once()
