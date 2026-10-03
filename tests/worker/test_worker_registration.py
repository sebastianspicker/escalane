"""Pin the ARQ worker registration contract."""

from __future__ import annotations

from escalane.worker.settings import WorkerSettings
from tests.support.assertions import expect


def test_worker_registration_is_stable() -> None:
    functions = [function.__name__ for function in WorkerSettings.functions]
    cron_jobs = [(job.name, job.job_id) for job in WorkerSettings.cron_jobs]

    expect(
        functions
        == [
            "alarm_created",
            "escalate",
            "alarm_acked",
            "alarm_state_changed",
            "process_alarm_event",
            "recover_incomplete_alarm_events",
        ]
    )
    expect(
        cron_jobs
        == [
            ("cron:recover_incomplete_alarm_events", "recover-incomplete-alarm-events"),
            ("cron:publish_worker_heartbeat", "publish-worker-heartbeat"),
        ]
    )
    expect(WorkerSettings.max_tries == 5)
