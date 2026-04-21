# Security Policy

## Supported Versions

We release patches for security vulnerabilities. The following versions are currently supported:

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: |
| 0.1.x   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it by creating a GitHub Security Advisory. We appreciate responsible disclosure and will work with you to resolve the issue.

## Security Features

### Authentication

- Admin API endpoints require a secure API key (`X-Admin-Key` header)
- The browser-based admin UI uses a short-lived Redis-backed session cookie issued by `/admin/login`
- Admin key must be configured via `ADMIN_API_KEY` environment variable
- `/metrics` is protected by the same admin API key requirement
- Empty admin key fails closed: API endpoints reject access and admin login cannot establish a session

### Rate Limiting

- Configurable rate limiting per device token (default: 10 requests/minute)
- Redis-based rate limiting for distributed systems

### IP Allowlisting

- Yealink endpoints support IP allowlisting
- Configurable via `YELK_IP_ALLOWLIST` environment variable
- An empty allowlist disables source-IP filtering; treat that as local-dev or trusted-network only

### Trusted Proxy

- Support for X-Forwarded-For header validation
- Configurable trusted proxy CIDRs to prevent IP spoofing

### Input Validation

- Pydantic-based request validation
- SQLAlchemy ORM for database queries (prevents SQL injection)
- Parameterized queries

### Security Headers

Security headers are applied unconditionally by `_install_security_headers_middleware`:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'`
- `Strict-Transport-Security` (HTTPS requests only)
- `Cache-Control: no-store` on ACK pages (`/a/...`) to prevent token caching

CORS is not configured. The service is designed for same-origin access behind a reverse proxy.

## Best Practices

1. **Use HTTPS in production** - Configure a reverse proxy with TLS
2. **Trust proxies explicitly** - Set `TRUSTED_PROXY_CIDRS` before relying on forwarded HTTPS or client IP headers
2. **Generate strong API keys** - Use random keys with sufficient entropy:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
3. **Restrict network access** - Use firewall rules to limit access to necessary IPs
4. **Regular updates** - Keep dependencies up to date
5. **Monitor logs** - Watch for unusual activity
6. **Backup regularly** - Maintain database backups

## Dependencies Security

We use `pip-audit` in CI to check for known vulnerabilities in dependencies.

```bash
cd services/alarm_broker && pip-audit
```
