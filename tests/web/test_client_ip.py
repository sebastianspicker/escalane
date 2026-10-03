"""Client-address resolution behind trusted proxies."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from escalane.web.deps import get_client_ip
from tests.support.assertions import expect

pytestmark = [pytest.mark.unit]


def test_trusted_proxy_ignores_forged_leftmost_forwarded_address() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"203.0.113.10, 198.51.100.25")],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )
    settings = MagicMock(trusted_proxy_cidrs="127.0.0.1/32")

    expect(get_client_ip(request, settings) == "198.51.100.25")


def test_missing_asgi_client_address_is_not_treated_as_loopback() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "server": ("test", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )

    expect(get_client_ip(request) is None)
