"""Prometheus-compatible metrics collection and rendering, including connection acquisition."""

from __future__ import annotations

from collections import Counter
from threading import Lock

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
_lock = Lock()
_acquisition_counts = [0] * len(BUCKETS)
_acquisition_count = 0
_acquisition_total = 0.0
_http_requests_total: Counter[tuple[str, str, str]] = Counter()
_http_request_duration_ms_total: Counter[tuple[str, str, str]] = Counter()
_events_total: Counter[str] = Counter()
_LATENCY_KINDS = frozenset({"http", "provider_delivery", "redis_enqueue"})
_histograms: dict[str, tuple[list[int], int, float]] = {}


def observe_latency(kind: str, seconds: float) -> None:
    """Observe seconds with fixed families and buckets; no delivery or target labels."""
    if kind not in _LATENCY_KINDS:
        raise ValueError("Unsupported latency kind")
    seconds = max(0.0, seconds)
    with _lock:
        counts, count, total = _histograms.get(kind, ([0] * len(BUCKETS), 0, 0.0))
        for index, boundary in enumerate(BUCKETS):
            if seconds <= boundary:
                counts[index] += 1
        _histograms[kind] = counts, count + 1, total + seconds


def observe_acquisition(seconds: float) -> None:
    """Observe one connection acquisition into the fixed latency buckets."""
    global _acquisition_count, _acquisition_total
    seconds = max(0.0, seconds)
    with _lock:
        _acquisition_count += 1
        _acquisition_total += seconds
        for index, boundary in enumerate(BUCKETS):
            if seconds <= boundary:
                _acquisition_counts[index] += 1


def acquisition_snapshot() -> tuple[list[int], int, float]:
    with _lock:
        return list(_acquisition_counts), _acquisition_count, _acquisition_total


def latency_snapshot() -> dict[str, tuple[list[int], int, float]]:
    with _lock:
        snapshot = {key: (list(value[0]), value[1], value[2]) for key, value in _histograms.items()}
    snapshot["connection_acquisition"] = acquisition_snapshot()
    return snapshot


def _render_histogram(
    kind: str, observation: tuple[list[int], int, float], *, worker: bool = False
) -> list[str]:
    counts, count, total = observation
    name = f"escalane_{'worker_' if worker else ''}{kind}_duration_seconds"
    description = "Latency in seconds."
    if kind == "connection_acquisition":
        description = "Connection acquisition including pool wait and connection setup in seconds."
    lines = [f"# HELP {name} {description}", f"# TYPE {name} histogram"]
    lines.extend(
        f'{name}_bucket{{le="{boundary}"}} {value}'
        for boundary, value in zip(BUCKETS, counts, strict=True)
    )
    lines.extend(
        [f'{name}_bucket{{le="+Inf"}} {count}', f"{name}_count {count}", f"{name}_sum {total}"]
    )
    return lines


def record_http_request(*, method: str, route: str, status_code: int, duration_ms: int) -> None:
    """Accumulate bounded request metrics under a lock for concurrent ASGI handlers."""
    method = method.upper()
    if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
        method = "OTHER"
    key = (method, route, str(status_code))
    observe_latency("http", duration_ms / 1000)
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
    gauges: dict[str, float] | None = None,
    worker_histograms: dict | None = None,
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

    for kind, observation in latency_snapshot().items():
        lines.extend(_render_histogram(kind, observation))
    for kind, observation in (worker_histograms or {}).items():
        if kind in _LATENCY_KINDS | {"connection_acquisition"}:
            lines.extend(_render_histogram(kind, observation, worker=True))
    for name, gauge_value in sorted((gauges or {}).items()):
        lines.extend([f"# TYPE escalane_{name} gauge", f"escalane_{name} {gauge_value}"])
    lines.append("")
    return "\n".join(lines)
