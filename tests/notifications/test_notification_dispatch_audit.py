"""Notification audit and retry-classification tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from escalane.notifications.delivery import NotificationDeliveryError, log_notification
from tests.support.assertions import expect
from tests.support.notifications import (
    ALARM_ID,
    NOW,
    make_alarm_double,
    make_enriched,
    make_service,
    make_target,
    noop_session,
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

    svc, session = make_service(), await noop_session()
    target = make_target(channel="signal", address="group-id")
    payload = svc._build_notification_payload(
        alarm=make_alarm_double(), enriched=make_enriched(), step_no=0, ack_url=None
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
    expect(log_result.await_args.args[3] == ("permanent_error" if expected_delivered else "error"))


# ── handle_zammad_ticket ───────────────────────────────────────────────


async def test_handle_zammad_ticket_disabled_returns_none():
    svc = make_service(zammad_enabled=False)
    session = await noop_session()
    session.scalar.return_value = None

    result = await svc.handle_zammad_ticket(
        session,
        alarm=make_alarm_double(),
        enriched=make_enriched(),
        ack_url=None,
    )

    expect(result is None)


async def test_handle_zammad_ticket_create_exception_requests_worker_retry():
    import httpx

    svc = make_service(zammad_enabled=True)
    request = httpx.Request("POST", "https://zammad.example.test/api/v1/tickets")
    svc._zammad.create_ticket = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "unavailable", request=request, response=httpx.Response(503, request=request)
        )
    )
    session = await noop_session()
    session.scalar.return_value = None

    with pytest.raises(NotificationDeliveryError, match="ticket creation"):
        await svc.handle_zammad_ticket(
            session,
            alarm=make_alarm_double(),
            enriched=make_enriched(),
            ack_url=None,
        )


async def test_handle_zammad_ticket_permanent_failure_is_audited_without_retry():
    import httpx

    svc = make_service(zammad_enabled=True)
    request = httpx.Request("POST", "https://zammad.example.test/api/v1/tickets")
    svc._zammad.create_ticket = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "invalid request", request=request, response=httpx.Response(400, request=request)
        )
    )
    session = await noop_session()

    result = await svc.handle_zammad_ticket(
        session,
        alarm=make_alarm_double(),
        enriched=make_enriched(),
        ack_url=None,
    )

    expect(result is None)
    session.commit.assert_awaited_once()


async def test_handle_zammad_ticket_success_returns_ticket_id():
    svc = make_service(zammad_enabled=True)
    svc._zammad.create_ticket = AsyncMock(return_value=42)
    session = await noop_session()

    result = await svc.handle_zammad_ticket(
        session,
        alarm=make_alarm_double(),
        enriched=make_enriched(),
        ack_url="http://x/a/tok",
    )

    expect(result == 42)


async def test_successful_connector_audit_failure_propagates_for_worker_retry():
    """A failed success audit must not be mistaken for a provider failure or swallowed."""
    svc = make_service()
    session = await noop_session()
    session.commit.side_effect = RuntimeError("database unavailable")
    session.rollback = AsyncMock()
    target = make_target(channel="signal", address="group-1")
    payload = svc._build_notification_payload(
        alarm=make_alarm_double(), enriched=make_enriched(), step_no=0, ack_url=None
    )

    with pytest.raises(NotificationDeliveryError, match="audit persistence"):
        await svc._send_via_signal(session, target, payload["body"], payload)

    svc._signal.send_group_message.assert_awaited_once()
    session.rollback.assert_awaited_once()


# ── add_zammad_ack_note ────────────────────────────────────────────────


async def test_add_zammad_ack_note_disabled_returns_false():
    svc = make_service(zammad_enabled=False)
    session = await noop_session()

    result = await svc.add_zammad_ack_note(
        session,
        alarm_id=ALARM_ID,
        ticket_id=10,
        acked_by="user",
        acked_at=NOW,
        note=None,
    )

    expect(result is False)


async def test_add_zammad_ack_note_permanent_exception_is_complete_without_retry():
    svc = make_service(zammad_enabled=True)
    svc._zammad.add_internal_note = AsyncMock(side_effect=RuntimeError("zammad error"))
    session = await noop_session()
    session.scalar.return_value = None

    result = await svc.add_zammad_ack_note(
        session,
        alarm_id=ALARM_ID,
        ticket_id=10,
        acked_by="user",
        acked_at=NOW,
        note="note text",
    )

    expect(result is True)
    expect(session.add.call_args.args[0].result == "permanent_error")


async def test_add_zammad_ack_note_transient_exception_requests_retry():
    import httpx

    svc = make_service(zammad_enabled=True)
    request = httpx.Request("PUT", "https://zammad.example.test/api/v1/tickets/10")
    svc._zammad.add_internal_note = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "unavailable", request=request, response=httpx.Response(503, request=request)
        )
    )
    session = await noop_session()
    session.scalar.return_value = None

    result = await svc.add_zammad_ack_note(
        session,
        alarm_id=ALARM_ID,
        ticket_id=10,
        acked_by="user",
        acked_at=NOW,
        note="note text",
    )

    expect(result is False)
    expect(session.add.call_args.args[0].result == "error")


# ── log_notification (module-level) ───────────────────────────────────


async def test_log_notification_module_fn():
    session = await noop_session()

    await log_notification(
        session,
        alarm_id=ALARM_ID,
        channel="sms",
        target_id="t1",
        payload={"msg": "test"},
        result="ok",
    )

    session.add.assert_called_once()
    session.commit.assert_called_once()
