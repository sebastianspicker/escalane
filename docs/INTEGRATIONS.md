# Integrations

## Yealink trigger intake

The trigger route is:

```text
GET /v1/yealink/alarm?token=<device-token>
```

`YELK_TOKEN_QUERY_PARAM` changes the query key. Outside simulation mode, the
request source must match `YELK_IP_ALLOWLIST`. Forwarded client addresses are
trusted only when the immediate peer matches `TRUSTED_PROXY_CIDRS`.

Each device token is stored with a configured device. The route applies a
per-device Redis rate limit controlled by `RATE_LIMIT_PER_MINUTE`.

Provision phones with a unique token per device. Do not reuse
`ADMIN_API_KEY`, log trigger URLs, or place tokens in public documentation.

## Zammad

Set `ZAMMAD_API_TOKEN` to activate the connector. The configured
`ZAMMAD_BASE_URL` must use HTTPS, must not contain URL credentials, and cannot
remain at the reserved example value.

The connector creates a ticket for an alarm and can update it as state changes.
Group, priority, state, and customer mappings are configured with:

- `ZAMMAD_GROUP`
- `ZAMMAD_PRIORITY_ID_P0`
- `ZAMMAD_STATE_ID_NEW`
- `ZAMMAD_CUSTOMER`

Validate credentials and field identifiers against the target Zammad instance.
The repository does not include a live-provider test.

## SendXMS

Set `SENDXMS_ENABLED=true`, `SENDXMS_API_KEY`, and a non-placeholder
`SENDXMS_BASE_URL`. Enabled endpoints require HTTPS and reject embedded URL
credentials.

`SENDXMS_FROM` sets the sender label. `SENDXMS_SEND_PATH` sets the relative
POST endpoint.

## Signal REST bridge

Set `SIGNAL_ENABLED=true`, `SIGNAL_CLI_ENDPOINT`, and
`SIGNAL_TARGET_GROUP_ID`. The bridge endpoint may use HTTP or HTTPS because
the checked-in Compose example expects a bridge on the private backend
network.

`SIGNAL_SEND_PATH` defaults to `/v2/send`. Escalane does not run or configure
the bridge service by default.

## Generic webhooks

There are two webhook uses:

- `WEBHOOK_URL` receives signed alarm state callbacks when
  `WEBHOOK_ENABLED=true`.
- Escalation targets with `channel: webhook` receive generic notification
  payloads.

Every destination host must be listed exactly in `WEBHOOK_ALLOWED_HOSTS`.
Wildcards are rejected. The state callback requires HTTPS and a
`WEBHOOK_SECRET` of at least 32 characters. Its JSON body is signed with
HMAC-SHA256 in `X-Hub-Signature-256`.

`ALLOW_HTTP_WEBHOOKS=true` permits HTTP only for generic escalation-target
webhooks. Host allowlisting and public-address validation remain active. Do not
enable it on an untrusted network.

## Simulation mode

`SIMULATION_ENABLED=true` replaces live connector delivery with mock records
and enables `/v1/simulation` plus `/admin/simulation`. Simulation requires a
loopback `BASE_URL`.

Use:

```bash
export ADMIN_API_KEY='<admin-api-key>'
make demo-prepare
```

The command uses `http://localhost:8080` by default and reads
`ADMIN_API_KEY` when `--admin-key` is not supplied. Run
`./.venv/bin/python scripts/demo_prepare.py --help` for options.

Simulation verifies application flow, not provider credentials, transport,
rate limits, idempotency, or delivery.
