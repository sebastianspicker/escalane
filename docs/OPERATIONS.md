# Operations Guide

This guide covers operational aspects of running Alarm Broker in production.

## Monitoring

### Health Endpoints

- `/healthz` - Liveness probe (basic health check)
- `/readyz` - Readiness probe (includes DB and Redis connectivity)

```bash
# Check health
curl -sS http://localhost:8080/healthz

# Check readiness
curl -sS http://localhost:8080/readyz
```

### Metrics

The application exposes Prometheus-compatible metrics at `/metrics`, but the endpoint is protected by the admin API key.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `alarm_broker_http_requests_total` | Counter | method, route, status_code | HTTP request count |
| `alarm_broker_http_request_duration_ms_total` | Counter | method, route, status_code | Cumulative request duration (ms) |
| `alarm_broker_alarms_by_status` | Gauge | status | Alarm count per lifecycle state |
| `alarm_broker_notifications_total` | Counter | channel, result | Notification attempts by channel and outcome |
| `alarm_broker_events_total` | Counter | event | Internal events (webhook_delivery_ok, etc.) |

Scrape guidance:

- Direct local check: `curl -sS http://localhost:8080/metrics -H 'X-Admin-Key: ...'`
- Production scraping: terminate auth at a trusted reverse proxy or scrape through a sidecar that injects `X-Admin-Key`

## Simulation Mode Operations

Simulation mode is intended for demos and non-production validation.

Required settings:
- `SIMULATION_ENABLED=true`
- `ADMIN_API_KEY` set (all simulation endpoints require admin auth)

Available admin-protected endpoints:
- `GET /v1/simulation/status`
- `GET /v1/simulation/notifications`
- `POST /v1/simulation/notifications/clear`
- `POST /v1/simulation/seed`

Behavior notes:
- If simulation mode is disabled, these endpoints return `404` by design.
- `POST /v1/simulation/seed` returns the bundled seed file path and points to `/v1/admin/seed`.
- Mock notifications are ephemeral and can be reset via `notifications/clear`.

## Logging

### Log Levels

Configure via `LOG_LEVEL` environment variable:
- `DEBUG` - Detailed debugging information
- `INFO` - General operational information
- `WARNING` - Warning messages
- `ERROR` - Error messages only

### Structured Logging

All logs are JSON-formatted with:
- `timestamp` - ISO 8601 timestamp
- `level` - Log level (debug, info, warning, error)
- `logger` - Source logger name
- `message` - Log message
- `extra` - Additional context fields

### Log Aggregation

For production, configure log shipping to a central logging system:

```yaml
# docker-compose.override.yml
services:
  api:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

## Backup & Restore

### Database Backup

```bash
# Full database dump
pg_dump -U alarm -h localhost -Fc alarm > backup_$(date +%Y%m%d).dump

# Restore from backup
pg_restore -U alarm -h localhost -d alarm -c backup_20240101.dump
```

### Redis Backup

Redis stores idempotency keys and rate limiting data. For critical data:

```bash
# Redis SAVE (synchronous)
redis-cli SAVE

# Copy dump file
cp /data/dump.rdb backup/dump_$(date +%Y%m%d).rdb
```

### Automated Backups

```bash
# /etc/cron.d/alarm-backup
0 2 * * * postgres pg_dump -U alarm -h localhost -Fc alarm > /backups/alarm_$(date +\%Y\%m\%d).dump
0 3 * * * root redis-cli SAVE && cp /var/lib/redis/dump.rdb /backups/redis_$(date +\%Y\%m\%d).rdb
```

## Performance Tuning

### Database Connection Pool

Pool settings are configured via environment variables (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`, `DB_POOL_RECYCLE`) and wired into `create_async_engine` in `db/engine.py`. The defaults (pool_size=5, max_overflow=10) are appropriate for low-traffic deployments. See `.env.example` for all pool tuning options.

### Worker Concurrency

arq worker concurrency is set in `WorkerSettings` in `worker/settings.py`. To change it, set the `max_jobs` class attribute. There's no env var for this currently.

## Troubleshooting

### Common Issues

#### Database Connection Errors

```
sqlalchemy.exc.OperationalError: could not connect to server
```

**Solution:** Check DATABASE_URL and network connectivity

```bash
# Test connection
psql -U alarm -h localhost -d alarm -c "SELECT 1"

# Check logs
docker compose logs db
```

#### Redis Connection Errors

```
redis.exceptions.ConnectionError: Error connecting to Redis
```

**Solution:** Check REDIS_URL and Redis availability

```bash
# Test connection
redis-cli -h localhost ping

# Check logs
docker compose logs redis
```

#### High Memory Usage

1. Check for memory leaks in worker processes
2. Review database query performance
3. Monitor Redis memory usage

```bash
# Redis memory usage
redis-cli INFO memory

# Active connections
redis-cli INFO clients
```

#### Slow Webhooks

1. Increase worker concurrency
2. Check network latency to webhook endpoints
3. Review webhook retry configuration

```bash
# Monitor webhook delivery events
curl -sS http://localhost:8080/metrics -H "X-Admin-Key: change-me-admin-key" | grep alarm_broker_events_total
```

### Debug Mode

Enable detailed logging:

```bash
LOG_LEVEL=DEBUG
```

### Database Query Analysis

```sql
-- Check slow queries
SELECT query, calls, mean_time, total_time
FROM pg_stat_statements
ORDER BY mean_time DESC
LIMIT 20;
```

## Capacity Planning

### Scaling Guidelines

| Users | Alarms/day | API Replicas | Worker Replicas | PostgreSQL | Redis |
|-------|------------|--------------|-----------------|------------|-------|
| 100   | 1,000      | 1            | 1               | 1 core, 2GB| 1 core, 512MB |
| 1,000 | 10,000     | 2            | 2               | 2 core, 4GB| 1 core, 1GB |
| 10,000| 100,000    | 4            | 4               | 4 core, 8GB| 2 core, 2GB |

### Monitoring Alerts

Set up alerts for:

- `/healthz` returning non-200 status
- `/readyz` returning non-200 status
- Database connection pool exhaustion (>80% utilized)
- Redis memory usage >80%
- High error rate (>1% of requests)
- Webhook failures >10%

## Maintenance

### Routine Maintenance

```bash
# Weekly: Check disk space
df -h

# Weekly: Review error logs
docker compose logs --since=7d | grep ERROR

# Monthly: Vacuum database
docker compose exec api psql -U alarm -d alarm -c "VACUUM ANALYZE;"
```

### Database Migration

Before running migrations, backup:

```bash
# Backup
pg_dump -U alarm -h localhost -Fc alarm > pre_migration_$(date +%Y%m%d).dump

# Run migration
docker compose exec api alembic upgrade head

# Verify
docker compose exec api alembic current
```

### Log Rotation

Configure in Docker:

```yaml
# docker-compose.yml
services:
  api:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "5"
```
