"""Pinned byte-webhook transport retry and permanent-error contracts."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from escalane.providers.webhook import post_webhook_bytes_to_validated_addresses


@pytest.mark.asyncio
async def test_byte_webhook_fails_over_with_hmac_headers_intact() -> None:
    attempts: list[tuple[str, dict[str, str]]] = []

    class Client:
        async def post(self, url: str, **kwargs: object) -> MagicMock:
            attempts.append((url, kwargs["headers"]))  # type: ignore[index]
            if len(attempts) == 1:
                raise httpx.ConnectError("temporary outage")
            return MagicMock(raise_for_status=MagicMock())

    await post_webhook_bytes_to_validated_addresses(
        Client(),
        "https://hooks.example.test/events",
        b'{"alarm":"a"}',
        {"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=abc"},
        5,
        "delivery-a",
        ("1.1.1.1", "8.8.8.8"),
        log_extra={"alarm_id": "a"},
    )

    assert [url for url, _headers in attempts] == [
        "https://1.1.1.1/events",
        "https://8.8.8.8/events",
    ]
    assert all(headers["Host"] == "hooks.example.test" for _url, headers in attempts)
    assert all(headers["X-Alarm-Delivery-ID"] == "delivery-a" for _url, headers in attempts)


@pytest.mark.asyncio
async def test_byte_webhook_stops_for_permanent_http_failure() -> None:
    request = httpx.Request("POST", "https://1.1.1.1/events")
    response = httpx.Response(400, request=request)

    class Client:
        async def post(self, *_args: object, **_kwargs: object) -> MagicMock:
            result = MagicMock()
            result.raise_for_status.side_effect = httpx.HTTPStatusError(
                "bad request", request=request, response=response
            )
            return result

    with pytest.raises(httpx.HTTPStatusError):
        await post_webhook_bytes_to_validated_addresses(
            Client(),
            "https://hooks.example.test/events",
            b"{}",
            {},
            5,
            "delivery-a",
            ("1.1.1.1", "8.8.8.8"),
            log_extra={"alarm_id": "a"},
        )
