# alarm-broker

Python package for the Alarm Broker service.

This package ships the FastAPI application, worker tasks, connectors, and the
packaged HTML templates required by the admin and ACK UIs.

## Source tour

- `alarm_broker/api/main.py` is the ASGI app factory and dependency bootstrap.
- `alarm_broker/api/routes/` contains HTTP entry points; most routes delegate business rules to `alarm_broker/services/`.
- `alarm_broker/services/trigger_service.py` is the inbound alarm orchestrator and the best starting point for understanding trigger idempotency, rate limiting, persistence, and event recovery.
- `alarm_broker/worker/tasks.py` contains ARQ background jobs for notification fan-out, escalation, ACK follow-up, state-change webhooks, and recovery scans.
- `alarm_broker/connectors/` contains thin HTTP clients for external systems; connector failures are logged as notification attempts instead of changing the alarm lifecycle.
- `alarm_broker/db/models.py` defines the persisted operational state and audit tables.
