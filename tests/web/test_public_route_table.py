"""Pin the public HTTP route table and its match order.

Expected values were captured from the pre-reconstruction application. Route
order matters: literal paths such as /v1/alarms/export must be matched
before parameterized siblings such as /v1/alarms/{alarm_id}.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from escalane.config.settings import Settings
from escalane.web.main import create_app
from tests.support.assertions import expect

EXPECTED_ROUTES = [
    ("MOUNT", "/admin/assets"),
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("GET", "/healthz/details"),
    ("GET", "/metrics"),
    ("GET", "/admin/login"),
    ("POST", "/admin/login"),
    ("POST", "/admin/logout"),
    ("POST", "/admin/session/extend"),
    ("GET", "/admin"),
    ("GET", "/admin/revision"),
    ("GET", "/admin/activity"),
    ("GET", "/admin/system"),
    ("GET", "/admin/simulation"),
    ("POST", "/admin/simulation/clear"),
    ("GET", "/admin/alarms/{alarm_id}"),
    ("GET", "/admin/alarms/{alarm_id}/drawer"),
    ("GET", "/admin/alarms/{alarm_id}/history"),
    ("POST", "/admin/alarms/{alarm_id}/ack"),
    ("POST", "/admin/alarms/{alarm_id}/resolve"),
    ("POST", "/admin/alarms/{alarm_id}/cancel"),
    ("POST", "/admin/alarms/{alarm_id}/notes"),
    ("POST", "/admin/alarms/{alarm_id}/delete"),
    ("POST", "/admin/alarms/bulk"),
    ("GET", "/admin/export"),
    ("GET", "/admin/configuration/escalation"),
    ("POST", "/admin/configuration/escalation"),
    ("GET", "/admin/configuration/import"),
    ("POST", "/admin/configuration/import"),
    ("GET", "/admin/configuration/{resource_name}"),
    ("POST", "/admin/configuration/{resource_name}/save"),
    ("POST", "/admin/configuration/{resource_name}/{resource_id}/deactivate"),
    ("POST", "/admin/configuration/{resource_name}/{resource_id}/delete"),
    ("GET", "/v1/yealink/alarm"),
    ("GET", "/a/{ack_token}"),
    ("POST", "/a/{ack_token}"),
    ("GET", "/v1/alarms"),
    ("GET", "/v1/alarms/export"),
    ("GET", "/v1/alarms/stats"),
    ("POST", "/v1/alarms/bulk/ack"),
    ("POST", "/v1/alarms/bulk/resolve"),
    ("POST", "/v1/alarms/bulk/cancel"),
    ("GET", "/v1/alarms/{alarm_id}"),
    ("PATCH", "/v1/alarms/{alarm_id}"),
    ("POST", "/v1/alarms/{alarm_id}/ack"),
    ("POST", "/v1/alarms/{alarm_id}/resolve"),
    ("POST", "/v1/alarms/{alarm_id}/cancel"),
    ("DELETE", "/v1/alarms/{alarm_id}"),
    ("GET", "/v1/alarms/{alarm_id}/notes"),
    ("POST", "/v1/alarms/{alarm_id}/notes"),
    ("POST", "/v1/admin/devices"),
    ("POST", "/v1/admin/escalation-policy"),
    ("POST", "/v1/admin/seed"),
    ("GET", "/v1/simulation/notifications"),
    ("POST", "/v1/simulation/notifications/clear"),
    ("GET", "/v1/simulation/status"),
    ("POST", "/v1/simulation/seed"),
]


def _flatten(routes: list[Any]) -> Iterator[tuple[str, str]]:
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _flatten(included.routes)
            continue
        methods = sorted(getattr(route, "methods", None) or ["MOUNT"])
        yield ",".join(methods), route.path


def test_http_route_table_and_order_are_stable(settings: Settings) -> None:
    app = create_app(settings=settings)

    routes = list(_flatten(app.routes))
    expect(routes == EXPECTED_ROUTES, routes)
