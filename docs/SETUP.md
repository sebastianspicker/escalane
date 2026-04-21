# Setup Guide

## Prerequisites

- Docker & Docker Compose
- Python 3.12+ (for local development)
- Make (optional, for convenience commands)

## Quick Start (Docker Compose)

```bash
# 1. Create environment file
cp .env.example .env
# Edit .env: set ADMIN_API_KEY, DATABASE_URL, REDIS_URL, BASE_URL

# 2. Start services
docker compose -f deploy/docker-compose.yml up -d --build

# 3. Run database migrations
docker compose -f deploy/docker-compose.yml exec api alembic upgrade head

# 4. Load seed data
curl -sS -X POST "http://localhost:8080/v1/admin/seed" \
  -H "X-Admin-Key: your-admin-key" \
  -H "Content-Type: application/x-yaml" \
  --data-binary @deploy/seed.example.yaml

# 5. Verify
curl -sS http://localhost:8080/readyz | jq .
```

## Development Workflow

### Running Tests

```bash
make test
# or directly:
./.venv/bin/pytest -q
```

### Code Quality

```bash
make lint    # Format and lint check
make audit   # Security audit
make clean   # Clean up
```

### Running Specific Tests

```bash
./.venv/bin/pytest -q -m security   # Security tests only
./.venv/bin/pytest -v               # Verbose output
./.venv/bin/pytest tests/test_api_flow.py  # Single file
```

## Database Migrations

```bash
# Create new migration
docker compose -f deploy/docker-compose.yml exec api alembic revision --autogenerate -m "description"

# Apply migrations
docker compose -f deploy/docker-compose.yml exec api alembic upgrade head

# Rollback
docker compose -f deploy/docker-compose.yml exec api alembic downgrade -1
```

## Production Deployment

### Database Setup

```sql
CREATE DATABASE alarm;
CREATE USER alarm WITH PASSWORD 'secure-password';
GRANT ALL PRIVILEGES ON DATABASE alarm TO alarm;
GRANT ALL ON SCHEMA public TO alarm;
```

### Security Recommendations

1. **Use HTTPS** - Configure a reverse proxy (nginx, traefik)
2. **Firewall** - Only expose port 80/443
3. **Secrets** - Use Docker secrets or external secret management
4. **Non-root** - Run containers as non-root user

### Reverse Proxy (nginx)

```nginx
server {
    listen 443 ssl http2;
    server_name alarm.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Upgrading

```bash
pg_dump -U alarm -h localhost alarm > backup_$(date +%Y%m%d).sql  # Backup first
git pull
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml exec api alembic upgrade head
docker compose -f deploy/docker-compose.yml restart
```

## Configuration Reference

All configuration is via environment variables. See `.env.example` for all options.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ADMIN_API_KEY` | Yes | - | Admin API key |
| `DATABASE_URL` | Yes | - | PostgreSQL connection URL |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Redis connection URL |
| `BASE_URL` | Yes | `http://localhost:8080` | Public base URL for ACK links |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `ENABLE_API_DOCS` | No | `false` | Enable `/docs` and `/redoc` |
| `YELK_IP_ALLOWLIST` | No | - | Comma-separated IPs/CIDRs for Yealink |
| `RATE_LIMIT_PER_MINUTE` | No | `10` | Rate limit per device token |

See `.env.example` for Zammad, SMS, Signal, and webhook configuration.

## Project Structure

```
alarm-broker/
├── deploy/                    # Docker Compose and seed examples
├── docs/                      # Documentation
├── services/
│   └── alarm_broker/
│       ├── alarm_broker/      # Main application package
│       │   ├── api/           # FastAPI routes and schemas
│       │   ├── connectors/    # External service clients
│       │   ├── core/          # Core utilities
│       │   ├── db/            # Database models and migrations
│       │   ├── services/      # Business logic layer
│       │   └── worker/        # Background task workers
│       ├── tests/             # Test suite
│       └── pyproject.toml     # Python dependencies
└── Makefile                   # Development commands
```

## API Endpoints

### Public

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/healthz` | GET | Liveness check |
| `/readyz` | GET | Readiness check (DB + Redis) |
| `/v1/yealink/alarm` | GET | Yealink alarm trigger |
| `/a/{ack_token}` | GET/POST | Alarm acknowledgment UI |
| `/admin/login` | GET/POST | Admin login form that sets a session cookie |

### Operator UI

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin` | GET | Admin dashboard (requires the `admin_session` cookie from `/admin/login`) |

### Admin (require `X-Admin-Key` header)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/alarms` | GET | List alarms (paginated) |
| `/v1/alarms/{id}` | GET | Get alarm details |
| `/v1/alarms/{id}/ack` | POST | Acknowledge alarm |
| `/v1/alarms/{id}/resolve` | POST | Resolve alarm |
| `/v1/alarms/{id}/cancel` | POST | Cancel alarm |
| `/v1/alarms/bulk/ack` | POST | Bulk acknowledge |
| `/v1/alarms/bulk/resolve` | POST | Bulk resolve |
| `/v1/alarms/bulk/cancel` | POST | Bulk cancel |
| `/metrics` | GET | Prometheus-style metrics |
| `/v1/admin/seed` | POST | Load seed data |

Cookie behavior:

- Local HTTP development on `http://localhost:8080` uses non-`Secure` admin and CSRF cookies so browser login and ACK flows work without TLS.
- HTTPS requests, or requests forwarded as HTTPS by a trusted proxy, use `Secure` cookies.

## Contributing

1. Create a feature branch
2. Make changes with tests
3. Run `make lint test audit`
4. Submit pull request

Planning policy: keep long-lived planning in `docs/ROADMAP.md`. Do not create ad-hoc plan files.
