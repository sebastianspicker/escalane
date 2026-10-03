"""Pinned byte-webhook transport retry and permanent-error contracts."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from escalane.providers.webhook import post_webhook_bytes_to_validated_addresses


class _StreamResult:
    def __init__(self, response: object | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error

    async def __aenter__(self) -> Any:
        if self.error is not None:
            raise self.error
        return self.response

    async def __aexit__(self, *_args: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_byte_webhook_fails_over_with_hmac_headers_intact() -> None:
    attempts: list[tuple[str, dict[str, str]]] = []

    class Client:
        def stream(self, _method: str, url: str, **kwargs: object) -> _StreamResult:
            attempts.append((url, kwargs["headers"]))  # type: ignore[index]
            if len(attempts) == 1:
                return _StreamResult(error=httpx.ConnectError("temporary outage"))
            return _StreamResult(MagicMock(raise_for_status=MagicMock()))

    await post_webhook_bytes_to_validated_addresses(
        "https://hooks.example.test/events",
        b'{"alarm":"a"}',
        {"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=abc"},
        ("1.1.1.1", "8.8.8.8"),
        delivery_id="delivery-a",
        timeout=5,
        address_failed_event="webhook_delivery_address_failed",
        log_extra={"alarm_id": "a"},
        client=Client(),  # type: ignore[arg-type]
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
        def stream(self, *_args: object, **_kwargs: object) -> _StreamResult:
            result = MagicMock()
            result.raise_for_status.side_effect = httpx.HTTPStatusError(
                "bad request", request=request, response=response
            )
            return _StreamResult(result)

    with pytest.raises(httpx.HTTPStatusError):
        await post_webhook_bytes_to_validated_addresses(
            "https://hooks.example.test/events",
            b"{}",
            {},
            ("1.1.1.1", "8.8.8.8"),
            delivery_id="delivery-a",
            timeout=5,
            address_failed_event="webhook_delivery_address_failed",
            log_extra={"alarm_id": "a"},
            client=Client(),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_byte_webhook_does_not_read_response_body_and_closes_stream() -> None:
    class UnreadableStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.closed = False

        async def __aiter__(self):
            raise AssertionError("response body must not be read")
            yield b"unreachable"

        async def aclose(self) -> None:
            self.closed = True

    response_stream = UnreadableStream()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, stream=response_stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await post_webhook_bytes_to_validated_addresses(
            "https://hooks.example.test/events",
            b"{}",
            {},
            ("1.1.1.1",),
            delivery_id="delivery-a",
            timeout=5,
            address_failed_event="webhook_delivery_address_failed",
            log_extra={"alarm_id": "a"},
            client=client,
        )

    assert response_stream.closed
