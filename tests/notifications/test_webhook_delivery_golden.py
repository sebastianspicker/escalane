"""Golden characterization of target-webhook and state-webhook delivery effects.

Both webhook paths share a security boundary but intentionally differ in wire
encoding, audit wording, telemetry, logs, and retry signalling. These tests pin
every externally observable effect byte-for-byte through stable seams only:
the event loop resolver, the HTTP transport (respx), the audit table, and the
telemetry counters.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from arq import Retry
from sqlalchemy import select

from escalane.notifications.delivery import NotificationDeliveryError
from escalane.notifications.dispatch import NotificationService
from escalane.persistence.models import (
    Alarm,
    AlarmNotification,
    EscalationPolicy,
    EscalationStep,
    EscalationTarget,
)
from escalane.providers.mock import MockSendXmsClient, MockSignalClient, MockZammadClient
from escalane.security.url_validation import RetryableSSRFError
from escalane.telemetry import metrics
from escalane.worker.tasks import alarm_state_changed
from tests.support.constants import TEST_WEBHOOK_SECRET
from tests.support.factories import make_alarm
from tests.support.worker import enable_webhook, make_ctx

pytestmark = [pytest.mark.unit]

ALARM_ID = uuid.UUID("0f0f0f0f-1111-4222-8333-444455556666")
CREATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
FROZEN_NOW = datetime(2026, 1, 2, 3, 5, 0, tzinfo=UTC)
TARGET_URL = "https://hooks.example.test/target"
STATE_URL = "https://hooks.example.test/state"
PINNED_TARGET_URL = "https://1.1.1.1/target"
PINNED_STATE_URL = "https://1.1.1.1/state"
ENRICHED = {
    "person_name": "Jörg Müller",
    "room_label": "Raum 1.23",
    "site_name": "Standort BG",
    "severity": "P0",
}


class _FrozenDatetime(datetime):
    """Freeze the state-webhook timestamp so its signed body is reproducible."""

    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        return FROZEN_NOW


def _resolver(monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    """Replace DNS on the running loop: public answer, private answer, or outage."""

    async def getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list[Any]:
        if outcome == "dns_failure":
            raise socket.gaierror(socket.EAI_AGAIN, "temporary failure")
        address = "10.0.0.7" if outcome == "ssrf" else "1.1.1.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    """Isolate telemetry event counters for exact per-test assertions."""
    counter: Counter[str] = Counter()
    monkeypatch.setattr(metrics, "_events_total", counter)
    return counter


async def _seed(sessionmaker: Any) -> None:
    async with sessionmaker() as session:
        session.add(EscalationPolicy(id="default", name="Default"))
        session.add(
            EscalationTarget(id="hook-target", label="Hook", channel="webhook", address=TARGET_URL)
        )
        session.add(
            EscalationStep(
                policy_id="default", step_no=1, after_seconds=60, target_id="hook-target"
            )
        )
        session.add(
            make_alarm(
                ALARM_ID,
                person_id="jörg-1",
                created_at=CREATED_AT,
                ack_token="golden-ack-token",
            )
        )
        await session.commit()


async def _audit_rows(sessionmaker: Any) -> list[dict[str, Any]]:
    async with sessionmaker() as session:
        rows = (
            await session.scalars(
                select(AlarmNotification)
                .where(AlarmNotification.alarm_id == ALARM_ID)
                .order_by(AlarmNotification.created_at, AlarmNotification.id)
            )
        ).all()
    return [
        {
            "channel": row.channel,
            "target_id": row.target_id,
            "result": row.result,
            "error": row.error,
            "payload": dict(row.payload),
        }
        for row in rows
    ]


def _logs(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str]]:
    return [
        (record.levelname, record.getMessage())
        for record in caplog.records
        if record.name == "escalane" and record.levelno >= logging.WARNING
    ]


async def _send_target(sessionmaker: Any, settings: Any) -> Exception | None:
    settings.webhook_allowed_hosts = "hooks.example.test"
    service = NotificationService(
        zammad=MockZammadClient(), sendxms=MockSendXmsClient(), signal=MockSignalClient()
    )
    async with sessionmaker() as session:
        alarm = await session.get(Alarm, ALARM_ID)
        try:
            await service.send(
                session,
                alarm,
                ENRICHED,  # type: ignore[arg-type]
                step_no=1,
                ack_url="http://localhost:8080/a/golden-ack-token",
                settings=settings,
            )
        except Exception as exc:
            return exc
    return None


async def _send_state(
    sessionmaker: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> Exception | None:
    enable_webhook(
        settings,
        url=STATE_URL,
        secret=TEST_WEBHOOK_SECRET,
        allowed_hosts="hooks.example.test",
    )
    monkeypatch.setattr("escalane.notifications.workflows.datetime", _FrozenDatetime)
    async with httpx.AsyncClient() as http:
        ctx = make_ctx(sessionmaker, settings, http)
        ctx["job_try"] = 1
        try:
            await alarm_state_changed(ctx, str(ALARM_ID), "acknowledged")
        except Exception as exc:
            return exc
    return None


def _route(router: respx.MockRouter, outcome: str, url: str) -> respx.Route:
    status = {"http_503": 503, "http_400": 400}.get(outcome, 200)
    return router.post(url).mock(return_value=httpx.Response(status))


def _requests(route: respx.Route) -> list[dict[str, Any]]:
    return [
        {
            "url": str(call.request.url),
            "host": call.request.headers.get("host"),
            "content-type": call.request.headers.get("content-type"),
            "x-alarm-delivery-id": call.request.headers.get("x-alarm-delivery-id"),
            "x-hub-signature-256": call.request.headers.get("x-hub-signature-256"),
            "body": call.request.content,
        }
        for call in route.calls
    ]


def _signal(error: Exception | None) -> tuple[str, ...] | None:
    if error is None:
        return None
    if isinstance(error, Retry):
        return ("Retry", str(error.defer_score), type(error.__cause__).__name__)
    return (type(error).__name__, str(error))


TARGET_DELIVERY_ID = "22eb8fd6-b3d0-50fa-9449-3616a5bf7d5a"
STATE_DELIVERY_ID = "9f651924-100b-52d5-9f38-459f4511ecf1"
TARGET_MESSAGE = (
    "NOTFALLALARM (silent)\n"
    "Alarm-ID: 0f0f0f0f-1111-4222-8333-444455556666\n"
    "Person: Jörg Müller\n"
    "Ort: Raum 1.23 / Standort BG\n"
    "Zeit: 2026-01-02T03:04:05\n"
    "Stufe: 1\n"
    "Quittieren: http://localhost:8080/a/golden-ack-token"
)
TARGET_AUDIT_PAYLOAD = {
    "title": "ESKALATION Stufe 1 - Jörg Müller - Raum 1.23",
    "body": TARGET_MESSAGE,
    "tags": ["silent"],
    "priority": 3,
    "step_no": 1,
    "alarm_id": "0f0f0f0f-1111-4222-8333-444455556666",
    "delivery_id": TARGET_DELIVERY_ID,
}
# httpx ``json=`` encoding: compact separators and raw UTF-8 for non-ASCII text.
TARGET_REQUEST = {
    "url": PINNED_TARGET_URL,
    "host": "hooks.example.test",
    "content-type": "application/json",
    "x-alarm-delivery-id": TARGET_DELIVERY_ID,
    "x-hub-signature-256": None,
    "body": (
        b'{"title":"ESKALATION Stufe 1 - J\xc3\xb6rg M\xc3\xbcller - Raum 1.23",'
        b'"body":"NOTFALLALARM (silent)\\nAlarm-ID: 0f0f0f0f-1111-4222-8333-444455556666'
        b"\\nPerson: J\xc3\xb6rg M\xc3\xbcller\\nOrt: Raum 1.23 / Standort BG"
        b"\\nZeit: 2026-01-02T03:04:05\\nStufe: 1"
        b'\\nQuittieren: http://localhost:8080/a/golden-ack-token",'
        b'"tags":["silent"],"priority":3,"step_no":1,'
        b'"alarm_id":"0f0f0f0f-1111-4222-8333-444455556666"}'
    ),
}
# State webhooks sign ``json.dumps`` bytes, which escape non-ASCII text.
STATE_REQUEST = {
    "url": PINNED_STATE_URL,
    "host": "hooks.example.test",
    "content-type": "application/json",
    "x-alarm-delivery-id": STATE_DELIVERY_ID,
    "x-hub-signature-256": (
        "sha256=3efdac6401484a9c1c701e915c5f023ed45ae93fb16cf522fe99e80c457ca9f7"
    ),
    "body": (
        b'{"event":"alarm.state_changed","alarm_id":"0f0f0f0f-1111-4222-8333-444455556666",'
        b'"state":"acknowledged","timestamp":"2026-01-02T03:05:00+00:00",'
        b'"created_at":"2026-01-02T03:04:05","acked_at":null,"resolved_at":null,'
        b'"cancelled_at":null,"person_id":"j\\u00f6rg-1","room_id":"bg-1.23",'
        b'"site_id":"bg","device_id":"ylk-t5-10023"}'
    ),
}
STATE_AUDIT_PAYLOAD = {"state": "acknowledged", "delivery_id": STATE_DELIVERY_ID}
TARGET_RETRY = (
    "NotificationDeliveryError",
    "Notification delivery failed for targets: hook-target",
)
DNS_ERROR = "Cannot resolve hostname 'hooks.example.test'"
SSRF_ERROR = "URL resolves to a blocked IP range or non-global address (resolved: 10.0.0.7)"


def _target_audit(result: str, error: str | None) -> list[dict[str, Any]]:
    return [
        {
            "channel": "webhook",
            "target_id": "hook-target",
            "result": result,
            "error": error,
            "payload": TARGET_AUDIT_PAYLOAD,
        }
    ]


def _state_audit(result: str, error: str | None) -> list[dict[str, Any]]:
    return [
        {
            "channel": "webhook",
            "target_id": None,
            "result": result,
            "error": error,
            "payload": STATE_AUDIT_PAYLOAD,
        }
    ]


TARGET_EXPECTED: dict[str, dict[str, Any]] = {
    "success": {
        "requests": [TARGET_REQUEST],
        "audit": _target_audit("ok", None),
        "events": {},
        "logs": [],
        "signal": None,
    },
    "dns_failure": {
        "requests": [],
        "audit": _target_audit("error", f"DNS resolution failed: {DNS_ERROR}"),
        "events": {"notification_delivery_error": 1},
        "logs": [("WARNING", "webhook_dns_unavailable")],
        "signal": TARGET_RETRY,
    },
    "ssrf": {
        "requests": [],
        "audit": _target_audit("skipped", f"SSRF blocked: {SSRF_ERROR}"),
        "events": {},
        "logs": [("WARNING", "webhook_ssrf_blocked")],
        "signal": None,
    },
    "http_503": {
        "requests": [TARGET_REQUEST],
        "audit": _target_audit("error", "Downstream provider returned HTTP 503"),
        "events": {"notification_delivery_error": 1},
        "logs": [
            ("WARNING", "webhook_notification_address_failed"),
            ("ERROR", "webhook_notification_failed"),
        ],
        "signal": TARGET_RETRY,
    },
    "http_400": {
        "requests": [TARGET_REQUEST],
        "audit": _target_audit("permanent_error", "Downstream provider returned HTTP 400"),
        "events": {},
        "logs": [
            ("WARNING", "webhook_notification_address_failed"),
            ("ERROR", "webhook_notification_failed"),
        ],
        "signal": None,
    },
}

STATE_EXPECTED: dict[str, dict[str, Any]] = {
    "success": {
        "requests": [STATE_REQUEST],
        "audit": _state_audit("ok", None),
        "events": {"webhook_delivery_ok": 1},
        "logs": [],
        "signal": None,
    },
    "dns_failure": {
        "requests": [],
        "audit": _state_audit("error", DNS_ERROR),
        "events": {"webhook_delivery_error": 1, "notification_delivery_retry": 1},
        "logs": [("WARNING", "notification_delivery_retry")],
        "signal": ("Retry", "1000", "RetryableSSRFError"),
    },
    "ssrf": {
        "requests": [],
        "audit": _state_audit("skipped", SSRF_ERROR),
        "events": {"webhook_delivery_error": 1},
        "logs": [("WARNING", "webhook_url_rejected")],
        "signal": None,
    },
    "http_503": {
        "requests": [STATE_REQUEST],
        "audit": _state_audit("error", "Downstream provider returned HTTP 503"),
        "events": {"webhook_delivery_error": 1, "notification_delivery_retry": 1},
        "logs": [
            ("WARNING", "webhook_delivery_address_failed"),
            ("ERROR", "webhook_delivery_failed"),
            ("WARNING", "notification_delivery_retry"),
        ],
        "signal": ("Retry", "1000", "NotificationDeliveryError"),
    },
    "http_400": {
        "requests": [STATE_REQUEST],
        "audit": _state_audit("permanent_error", "Downstream provider returned HTTP 400"),
        "events": {"webhook_delivery_error": 1},
        "logs": [("ERROR", "webhook_delivery_failed")],
        "signal": None,
    },
}


@pytest.mark.parametrize("outcome", sorted(TARGET_EXPECTED))
async def test_target_webhook_delivery_effects_are_pinned(
    outcome: str,
    sessionmaker: Any,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    events: Counter[str],
) -> None:
    """Escalation-target webhooks keep their wire body, audit, logs, and retry signal."""
    await _seed(sessionmaker)
    _resolver(monkeypatch, outcome)
    caplog.set_level(logging.INFO, logger="escalane")
    with respx.mock(assert_all_called=False) as router:
        route = _route(router, outcome, PINNED_TARGET_URL)
        error = await _send_target(sessionmaker, settings)

    observed = {
        "requests": _requests(route),
        "audit": await _audit_rows(sessionmaker),
        "events": dict(events),
        "logs": _logs(caplog),
        "signal": _signal(error),
    }
    assert observed == TARGET_EXPECTED[outcome]
    assert error is None or isinstance(error, NotificationDeliveryError)


@pytest.mark.parametrize("outcome", sorted(STATE_EXPECTED))
async def test_state_webhook_delivery_effects_are_pinned(
    outcome: str,
    sessionmaker: Any,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    events: Counter[str],
) -> None:
    """Signed state webhooks keep their wire body, audit, telemetry, and ARQ retry."""
    await _seed(sessionmaker)
    _resolver(monkeypatch, outcome)
    caplog.set_level(logging.INFO, logger="escalane")
    with respx.mock(assert_all_called=False) as router:
        route = _route(router, outcome, PINNED_STATE_URL)
        error = await _send_state(sessionmaker, settings, monkeypatch)

    observed = {
        "requests": _requests(route),
        "audit": await _audit_rows(sessionmaker),
        "events": dict(events),
        "logs": _logs(caplog),
        "signal": _signal(error),
    }
    assert observed == STATE_EXPECTED[outcome]
    if outcome == "dns_failure":
        assert isinstance(error, Retry)
        assert isinstance(error.__cause__, RetryableSSRFError)
