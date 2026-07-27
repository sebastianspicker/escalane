"""Tests for state-webhook payloads and pinned transport behavior."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx
from sqlalchemy import select

from escalane import constants
from escalane.db.models import Alarm, AlarmNotification
from escalane.services.notification_delivery import (
    NotificationDeliveryError,
    log_notification,
    notification_delivery_id,
)
from escalane.worker.task_workflows import (
    WebhookDelivery,
    build_webhook_payload,
    post_to_validated_addresses,
    send_state_webhook,
)
from escalane.worker.tasks import alarm_state_changed

try:
    from tests.assertions import expect
    from tests.constants import TEST_WEBHOOK_SECRET, value_for_test
    from tests.worker_task_helpers import (
        enable_webhook,
        make_alarm,
        make_webhook_context,
        persist_alarm,
    )
except ModuleNotFoundError:
    from assertions import expect
    from constants import TEST_WEBHOOK_SECRET, value_for_test
    from worker_task_helpers import (
        enable_webhook,
        make_alarm,
        make_webhook_context,
        persist_alarm,
    )

pytestmark = pytest.mark.unit


async def test_build_webhook_payload(sessionmaker, seeded_db):
    alarm_id = uuid.uuid4()
    now = datetime.now(UTC)

    await persist_alarm(sessionmaker, make_alarm(alarm_id, created_at=now))

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        payload = build_webhook_payload(alarm, "triggered")

    expect(payload["event"] == constants.EVENT_ALARM_STATE_CHANGED)
    expect(payload["alarm_id"] == str(alarm_id))
    expect(payload["state"] == "triggered")
    expect(payload["person_id"] == "ma-012")
    expect(payload["room_id"] == "bg-1.23")
    expect(payload["site_id"] == "bg")
    expect(payload["device_id"] == "ylk-t5-10023")
    expect(payload["created_at"] is not None)
    expect(payload["acked_at"] is None)
    expect(payload["resolved_at"] is None)
    expect(payload["cancelled_at"] is None)


def test_state_webhook_delivery_id_changes_with_state() -> None:
    alarm_id = uuid.uuid4()

    triggered = notification_delivery_id(
        alarm_id=alarm_id,
        channel="webhook",
        target_id=None,
        payload={"state": "triggered"},
    )
    resolved = notification_delivery_id(
        alarm_id=alarm_id,
        channel="webhook",
        target_id=None,
        payload={"state": "resolved"},
    )

    expect(triggered != resolved)


async def test_alarm_state_changed_posts_webhook_with_hmac(
    sessionmaker, seeded_db, settings, monkeypatch
):
    alarm_id = uuid.uuid4()
    now = datetime.now(UTC)

    await persist_alarm(sessionmaker, make_alarm(alarm_id, created_at=now))

    enable_webhook(
        settings,
        url="https://hooks.example.test/hmac",
        secret=TEST_WEBHOOK_SECRET,
        allowed_hosts="hooks.example.test",
    )

    http, ctx = make_webhook_context(sessionmaker, settings, monkeypatch)

    with respx.mock(assert_all_called=True) as mock_router:

        def check_hmac(request: httpx.Request) -> httpx.Response:
            sig_header = request.headers.get("X-Hub-Signature-256", "")
            expect(sig_header.startswith("sha256="))
            sig = sig_header.removeprefix("sha256=")
            expected = hmac.new(
                TEST_WEBHOOK_SECRET.encode(), request.content, hashlib.sha256
            ).hexdigest()
            expect(sig == expected)
            return httpx.Response(200, json={"ok": True})

        mock_router.post("https://1.1.1.1/hmac").mock(side_effect=check_hmac)
        await alarm_state_changed(ctx, str(alarm_id), "triggered")

    await http.aclose()


async def test_send_state_webhook_handles_retryable_failure(sessionmaker, seeded_db):
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(make_alarm(alarm_id))
        await session.commit()

    http = httpx.AsyncClient(verify=False)
    payload_dict = {"event": "alarm.state_changed", "alarm_id": str(alarm_id), "state": "triggered"}
    payload_bytes = json.dumps(payload_dict, separators=(",", ":")).encode()

    with respx.mock as mock_router:
        mock_router.post("https://1.1.1.1/fail").respond(500, text="Internal Error")

        async with sessionmaker() as session:
            with pytest.raises(NotificationDeliveryError, match="webhook delivery"):
                await send_state_webhook(
                    http=http,
                    webhook_url="https://hooks.example.test/fail",
                    payload_bytes=payload_bytes,
                    headers={"Content-Type": "application/json"},
                    timeout=5.0,
                    delivery=WebhookDelivery(alarm_id=alarm_id, session=session, state="triggered"),
                    resolved_addresses=("1.1.1.1",),
                    log_notification=log_notification,
                )

            row = await session.scalar(
                select(AlarmNotification)
                .where(AlarmNotification.alarm_id == alarm_id)
                .where(AlarmNotification.channel == "webhook")
            )

    expect(row is not None)
    expect(row.result == "error")
    expect(row.error is not None)

    await http.aclose()


async def test_state_webhook_fails_over_to_second_validated_address(sessionmaker, seeded_db):
    """State callbacks keep Host/SNI while moving from a failed pin to the next pin."""
    alarm_id = uuid.uuid4()
    calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

    async def post_once(_http, url, _payload, headers, _timeout, extensions):
        calls.append((url, headers, extensions))
        if "1.1.1.1" in url:
            raise httpx.ConnectError(
                "first address unavailable",
                request=httpx.Request("POST", url),
            )

    async with sessionmaker() as session:
        session.add(make_alarm(alarm_id))
        await session.commit()
        await post_to_validated_addresses(
            object(),
            "https://hooks.example.test/state",
            b"{}",
            {"Content-Type": "application/json"},
            0.01,
            WebhookDelivery(alarm_id=alarm_id, session=session, state="triggered"),
            ("1.1.1.1", "8.8.8.8"),
            post=post_once,
        )

    expect([call[0] for call in calls] == ["https://1.1.1.1/state", "https://8.8.8.8/state"])
    expect(all(call[1]["Host"] == "hooks.example.test" for call in calls))
    expect(all(call[2]["sni_hostname"] == "hooks.example.test" for call in calls))


async def test_state_webhook_logs_one_safe_error_after_all_validated_addresses_fail(
    sessionmaker, seeded_db
):
    """State callbacks do not use the hostname after each validated pin fails."""
    alarm_id = uuid.uuid4()
    secret = value_for_test("state-webhook-query")
    calls: list[str] = []

    async def fail_every_address(_http, url, _payload, _headers, _timeout, _extensions):
        calls.append(url)
        raise httpx.ConnectError(
            f"delivery failed for {url}",
            request=httpx.Request("POST", url),
        )

    async with sessionmaker() as session:
        session.add(make_alarm(alarm_id))
        await session.commit()
        with pytest.raises(NotificationDeliveryError, match="webhook delivery"):

            async def fail_all(*args, **kwargs):
                return await post_to_validated_addresses(
                    *args,
                    **kwargs,
                    post=fail_every_address,
                )

            await send_state_webhook(
                http=object(),
                webhook_url=f"https://hooks.example.test/state?token={secret}",
                payload_bytes=b"{}",
                headers={"Content-Type": "application/json"},
                timeout=0.01,
                delivery=WebhookDelivery(alarm_id=alarm_id, session=session, state="triggered"),
                resolved_addresses=("1.1.1.1", "8.8.8.8"),
                post_to_addresses=fail_all,
                log_notification=log_notification,
            )

        row = await session.scalar(
            select(AlarmNotification)
            .where(AlarmNotification.alarm_id == alarm_id)
            .where(AlarmNotification.channel == "webhook")
        )

    expect(
        calls
        == [
            f"https://1.1.1.1/state?token={secret}",
            f"https://8.8.8.8/state?token={secret}",
        ]
    )
    expect(row is not None)
    expect(row.error is not None)
    expect(secret not in row.error)


async def test_pinned_webhook_failure_redacts_query_from_audit_and_logs(
    sessionmaker,
    seeded_db,
    caplog,
):
    alarm_id = uuid.uuid4()
    secret = value_for_test("webhook-query")
    provider_detail = "private-provider-response-value"

    class FailingHttp:
        async def post(self, url, **_kwargs):  # noqa: ANN001
            raise httpx.ConnectError(
                f"request failed with {provider_detail} for {url}",
                request=httpx.Request("POST", url),
            )

    async with sessionmaker() as session:
        session.add(make_alarm(alarm_id))
        await session.commit()

        caplog.set_level(logging.WARNING, logger="escalane")
        with pytest.raises(NotificationDeliveryError, match="webhook delivery"):
            await send_state_webhook(
                http=FailingHttp(),
                webhook_url=f"https://hooks.example.test/fail?token={secret}",
                payload_bytes=b"{}",
                headers={"Content-Type": "application/json"},
                timeout=0.01,
                delivery=WebhookDelivery(alarm_id=alarm_id, session=session, state="triggered"),
                resolved_addresses=("203.0.113.10",),
                log_notification=log_notification,
            )

        row = await session.scalar(
            select(AlarmNotification)
            .where(AlarmNotification.alarm_id == alarm_id)
            .where(AlarmNotification.channel == "webhook")
        )

    assert row is not None
    assert row.error == "Downstream provider transport error"
    assert secret not in row.error
    assert provider_detail not in row.error
    assert secret not in caplog.text
    assert provider_detail not in caplog.text
