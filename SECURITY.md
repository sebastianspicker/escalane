# Security policy

## Supported versions

Until the first prerelease is published, security fixes are assessed for the
current default branch. After publication, only the current default branch and
latest prerelease are supported. Older tags are not maintained.

## Report a vulnerability

Do not open a public issue, discussion, pull request, or commit for a suspected
vulnerability. Use the repository Security tab to open a private vulnerability
report when private reporting is available. If it is unavailable, contact the
repository owner privately before sending technical details. If no private
channel is available, open a public issue requesting private contact without
including vulnerability details.

Include a minimal reproduction, affected version or commit, impact, and known
mitigations. Remove credentials, acknowledgement links, device tokens, alarm
data, personal data, and internal hostnames.

## Trust boundaries

- Admin JSON endpoints require `X-Admin-Key`.
- The operator console uses a Redis-backed session and CSRF protection for
  mutating forms.
- An empty `ADMIN_API_KEY` fails closed.
- A responder acknowledgement URL is a bearer capability.
- Yealink ingress requires a device token and, outside simulation, a source
  address in `YELK_IP_ALLOWLIST`.
- Forwarded client and scheme headers are accepted only from
  `TRUSTED_PROXY_CIDRS`.
- `/metrics` and `/healthz/details` require the admin key.

Do not expose static admin credentials, trigger URLs, acknowledgement URLs, or
connector secrets in logs, screenshots, issues, or audit data.

## Network and URL validation

`BASE_URL` must be an origin without URL credentials, path, query, or fragment.
Non-loopback origins require HTTPS. Simulation mode requires a loopback host.

Enabled Zammad and SendXMS endpoints require HTTPS and reject embedded URL
credentials. Enabled signed webhook callbacks require:

- an HTTPS `WEBHOOK_URL`
- the exact destination host in `WEBHOOK_ALLOWED_HOSTS`
- a `WEBHOOK_SECRET` of at least 32 characters

Generic escalation-target webhooks also require an exact allowed host and pass
public-address checks. `ALLOW_HTTP_WEBHOOKS=true` permits HTTP for that generic
target path only. It does not relax host or address validation and does not
apply to `WEBHOOK_URL`.

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

CORS is not configured. Browser operation is designed for same-origin access
behind a reverse proxy.

## Data handling

- PostgreSQL contains alarm, identity, location, configuration, and audit data.
- Redis contains queues, sessions, idempotency state, and rate-limit state.
- Provider errors are reduced to bounded diagnostic categories before
  persistence.
- Seed imports enforce byte, nesting, node, alias, and placeholder limits.

Define retention, access, backup, restoration, and deletion procedures for the
deployment environment. Do not use repository sample contact values as live
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
6. Use immutable images and apply migrations before API and worker rollout.
7. Test provider idempotency, backup and restore, rollback, and alert routing.
8. Review log forwarding and retention for sensitive fields.

## Dependency checks

```bash
make audit
```

This runs Ruff, Bandit on the application package, and `pip-audit` from
`services/escalane`. Advisory scanning does not replace a deployment inventory
or update policy.
