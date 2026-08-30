# Integrations

## Device trigger intake

The Yealink-compatible trigger route is:

```text
GET /v1/yealink/alarm?token=<device-token>
```

`YELK_TOKEN_QUERY_PARAM` changes the query key. Outside simulation mode, the
request source must match `YELK_IP_ALLOWLIST`. Forwarded client addresses are
accepted only when the immediate peer is in `TRUSTED_PROXY_CIDRS`.

Each device has its own token. The web adapter applies the Redis-backed
per-device limit configured by `RATE_LIMIT_PER_MINUTE`. Do not reuse an admin
key as a device token or log trigger URLs.

## Delivery providers

Provider clients belong in `src/escalane/providers/`; notification policy and
delivery audit belong in `src/escalane/notifications/`. The worker invokes
providers after an outbox event reaches ARQ.

| Provider | Required configuration |
|---|---|
| Zammad | `ZAMMAD_API_TOKEN` and a valid HTTPS `ZAMMAD_BASE_URL` |
| SendXMS | `SENDXMS_ENABLED=true`, `SENDXMS_API_KEY`, and a valid HTTPS `SENDXMS_BASE_URL` |
| Signal REST bridge | `SIGNAL_ENABLED=true`, `SIGNAL_CLI_ENDPOINT`, and `SIGNAL_TARGET_GROUP_ID` |
| State callback | `WEBHOOK_ENABLED=true`, HTTPS `WEBHOOK_URL`, `WEBHOOK_SECRET`, and an allowed host |
| Generic webhook target | Exact host in `WEBHOOK_ALLOWED_HOSTS`; HTTP only in simulation mode |

Zammad and SendXMS reject URL credentials and require HTTPS. The Signal bridge
may be HTTP only on a controlled private network. The state callback is signed
with HMAC-SHA256 in `X-Hub-Signature-256`. Generic webhook targets still require
host allowlisting and public-address validation when HTTP is permitted.

Validate credentials, provider field identifiers, idempotency, and delivery
behaviour in the target environment. The local suite does not prove live
provider behaviour.

## Simulation

`SIMULATION_ENABLED=true` substitutes mock delivery and enables simulation
surfaces. It requires a loopback `BASE_URL`. Simulation exercises application
flow, not provider credentials, transport, rate limits, idempotency, or
delivery.
