"""Prometheus-compatible metrics collection and rendering."""

from __future__ import annotations

from collections import Counter
from threading import Lock

_lock = Lock()
_http_requests_total: Counter[tuple[str, str, str]] = Counter()
_http_request_duration_ms_total: Counter[tuple[str, str, str]] = Counter()
_events_total: Counter[str] = Counter()


def record_http_request(*, method: str, route: str, status_code: int, duration_ms: int) -> None:
    """Accumulate bounded request metrics under a lock for concurrent ASGI handlers."""
    key = (method.upper(), route, str(status_code))
    with _lock:
        _http_requests_total[key] += 1
        _http_request_duration_ms_total[key] += max(0, int(duration_ms))


def record_event(event: str) -> None:
    """Increment an internal-event counter without requiring an external metrics service."""
    with _lock:
        _events_total[event] += 1


def _escape(value: str) -> str:
    """Escape label values for Prometheus's quoted text exposition format."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _http_labels(method: str, route: str, status_code: str) -> str:
    """Build consistently escaped labels for request metric families."""
    return (
        f'method="{_escape(method)}",route="{_escape(route)}",status_code="{_escape(status_code)}"'
    )


def render_prometheus_metrics(
    *,
    alarm_counts: dict[str, int],
    notification_counts: list[tuple[str, str, int]],
) -> str:
    """Render Prometheus text format metrics.

    Args:
        alarm_counts: Alarm counts by status (from metrics_queries.get_alarm_counts)
        notification_counts: Notification counts by channel/result (from metrics_queries)
    """
    lines: list[str] = []

    lines.append("# HELP escalane_http_requests_total Total number of HTTP requests.")
    lines.append("# TYPE escalane_http_requests_total counter")
    with _lock:
        http_requests_snapshot = dict(_http_requests_total)
        http_duration_snapshot = dict(_http_request_duration_ms_total)
        events_snapshot = dict(_events_total)

    for (method, route, status_code), value in sorted(http_requests_snapshot.items()):
        labels = _http_labels(method, route, status_code)
        lines.append(f"escalane_http_requests_total{{{labels}}} {value}")

    lines.append(
        "# HELP escalane_http_request_duration_ms_total Total request duration in milliseconds."
    )
    lines.append("# TYPE escalane_http_request_duration_ms_total counter")
    for (method, route, status_code), value in sorted(http_duration_snapshot.items()):
        labels = _http_labels(method, route, status_code)
        lines.append(f"escalane_http_request_duration_ms_total{{{labels}}} {value}")

    lines.append("# HELP escalane_events_total Total number of internal events.")
    lines.append("# TYPE escalane_events_total counter")
    for event, value in sorted(events_snapshot.items()):
        lines.append(f'escalane_events_total{{event="{_escape(event)}"}} {value}')

    lines.append("# HELP escalane_alarms_by_status Number of alarms by status.")
    lines.append("# TYPE escalane_alarms_by_status gauge")
    for state, count in sorted(alarm_counts.items()):
        lines.append(f'escalane_alarms_by_status{{status="{_escape(state)}"}} {count}')

    lines.append(
        "# HELP escalane_notifications_total Notification attempts grouped by channel/result."
    )
    lines.append("# TYPE escalane_notifications_total counter")
    for channel, result, count in notification_counts:
        lines.append(
            "escalane_notifications_total"
            f'{{channel="{_escape(channel)}",result="{_escape(result)}"}} {count}'
        )

    lines.append("")
    return "\n".join(lines)
