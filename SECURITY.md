# Security policy

## Supported versions

Before the first prerelease, maintainers assess security fixes against the
default branch. Once a prerelease is published, support covers the default
branch and the latest prerelease. Older tags are not maintained.

## Report a vulnerability

Do not open a public issue, discussion, pull request, or commit for a suspected
vulnerability. Use the repository Security tab to open a private vulnerability
report when private reporting is available. If it is unavailable, contact the
repository owner privately before sending technical details. If no private
channel is available, open a public issue requesting private contact without
including vulnerability details.

Describe how to reproduce the issue, which version or commit is affected,
what the impact could be, and any workaround you know of. Remove credentials, acknowledgement links, device tokens, alarm
data, personal data, and internal hostnames.

## Authentication and access

- Admin JSON endpoints require `X-Admin-Key`.
- The operator console uses a Redis-backed session and CSRF protection for
  mutating forms.
- An empty `ADMIN_API_KEY` does not grant admin access.
- Anyone with a responder acknowledgement URL can use its token to acknowledge
  that alarm. Treat the whole URL as a secret.
- Yealink triggers require a device token and, outside simulation, a source
  address in `YELK_IP_ALLOWLIST`.
- Forwarded client and scheme headers are accepted only from
  `TRUSTED_PROXY_CIDRS`.
- `/metrics` and `/healthz/details` require the admin key.

Do not expose static admin credentials, trigger URLs, acknowledgement URLs, or
provider secrets in logs, screenshots, issues, or audit data.

## Network and URL validation

`BASE_URL` must contain only the scheme, host, and optional port, without URL
credentials, a path, a query, or a fragment. It must use HTTPS unless the host
is loopback. Simulation mode requires a loopback host.

Enabled Zammad and SendXMS endpoints require HTTPS and reject embedded URL
credentials. Enabled signed webhook callbacks require:

- an HTTPS `WEBHOOK_URL`
- the exact destination host in `WEBHOOK_ALLOWED_HOSTS`
- a `WEBHOOK_SECRET` of at least 32 characters

Webhooks configured as escalation targets also require an exact allowed host
and must pass the public-address checks. HTTP is permitted only when simulation mode is enabled;
that does not relax host or address validation and does not apply to
`WEBHOOK_URL`.

The signed callback uses HMAC-SHA256 in `X-Hub-Signature-256`.

## HTTP controls

The API sets:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- a same-origin Content Security Policy
- a restrictive `Permissions-Policy`
- `Strict-Transport-Security` for HTTPS requests
- `Cache-Control: no-store` and `Pragma: no-cache` on acknowledgement pages

CORS is not configured. Serve the browser interface and API from the same
origin behind a reverse proxy.

## Data handling

- PostgreSQL contains alarm, identity, location, configuration, and audit data.
- Redis contains queues, sessions, idempotency state, and rate-limit state.
- Stored provider errors use a limited set of diagnostic categories rather
  than raw error responses.
- Seed imports limit input size, nesting, and node count, reject aliases, and
  restrict where placeholders can be used.

Decide who can access this data, how long to retain it, and how to back it up,
restore it, and delete it in your deployment. Do not use repository sample contact values as live
configuration.

## Deployment checklist

1. Generate a random admin key:

   ```bash
   python3.14 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. Store credentials outside images and source control.
3. Terminate TLS at a controlled reverse proxy.
4. Restrict API, database, Redis, and connector network access.
5. Configure `TRUSTED_PROXY_CIDRS` and `YELK_IP_ALLOWLIST` narrowly.
6. Use immutable images and apply migrations before starting the new API and worker.
7. Test provider idempotency, backup and restore, rollback, and alert routing.
8. Review log forwarding and retention for sensitive fields.

## Dependency checks

```bash
make audit
```

This runs Bandit on `src/escalane` and the repository scripts, then runs
`pip-audit`. Ruff is part of `make lint`. You still need to track the software
running in your deployment and decide how to keep it updated.
