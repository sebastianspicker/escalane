from __future__ import annotations

import uuid


def _token(label: str, value: str) -> str:
    return f"{label}-{value}"


def value_for_test(label: str) -> str:
    return _token(label, uuid.uuid5(uuid.NAMESPACE_DNS, f"alarm-broker-test-{label}").hex)


EMPTY_SECRET_VALUE = str()  # noqa: UP018
TEST_ADMIN_API_KEY = value_for_test("admin")
TEST_DEVICE_TOKEN = _token("device", uuid.UUID("11111111-1111-4111-8111-111111111111").hex)
TEST_ZAMMAD_TOKEN = _token("zammad", uuid.UUID("22222222-2222-4222-8222-222222222222").hex)
TEST_SMS_KEY = _token("sms", uuid.UUID("33333333-3333-4333-8333-333333333333").hex)
TEST_WEBHOOK_SECRET = _token("hmac", uuid.UUID("44444444-4444-4444-8444-444444444444").hex)
ACK_FOUND_TOKEN = _token("ack", uuid.UUID("55555555-5555-4555-8555-555555555555").hex)
ACK_SOFT_DELETED_TOKEN = _token("ack", uuid.UUID("66666666-6666-4666-8666-666666666666").hex)
ACK_ADMIN_HTML_TOKEN = _token("ack", uuid.UUID("77777777-7777-4777-8777-777777777777").hex)
