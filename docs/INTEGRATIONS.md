# Integrations

## Device trigger intake

The Yealink-compatible trigger route is:

```text
GET /v1/yealink/alarm?token=<device-token>
```

Set `YELK_TOKEN_QUERY_PARAM` to use a different query key. Outside simulation
mode, the request must come from an address in `YELK_IP_ALLOWLIST`. Escalane
accepts a forwarded client address only when the immediate peer belongs to
`TRUSTED_PROXY_CIDRS`.

Give each device its own token. Escalane uses Redis to enforce the per-device
limit in `RATE_LIMIT_PER_MINUTE`. Keep admin keys separate from device tokens,
and never log a trigger URL because it contains the device credential.

## Delivery providers

Provider clients live in `src/escalane/providers/`. The notification package in
`src/escalane/notifications/` decides what to send and records the result. Once
ARQ receives an outbox event, the worker passes the notification to the selected
provider.

Escalane stores escalation routes in PostgreSQL instead of hard-coding them. A
policy links each escalation step to enabled `email`, `sms`, `signal`, or
`webhook` targets. Environment variables configure the provider connections;
seed imports or the operator console set the targets and delays for a particular
deployment. The sample seed contains placeholders, so replace those values
before using it outside a demonstration.

| Provider | Required configuration |
|---|---|
| Zammad | `ZAMMAD_API_TOKEN`, HTTPS `ZAMMAD_BASE_URL`, and deployment-specific group, customer, priority, and state values |
| SendXMS | `SENDXMS_ENABLED=true`, `SENDXMS_API_KEY`, and HTTPS `SENDXMS_BASE_URL`; optional sender/path overrides |
| Signal REST bridge | `SIGNAL_ENABLED=true`, `SIGNAL_CLI_ENDPOINT`, `SIGNAL_TARGET_GROUP_ID`, and optional path override |
| State callback | `WEBHOOK_ENABLED=true`, HTTPS `WEBHOOK_URL`, `WEBHOOK_SECRET`, and an allowed host |
| Generic webhook target | Exact host in `WEBHOOK_ALLOWED_HOSTS`; HTTP only in simulation mode |

Zammad and SendXMS URLs cannot contain credentials and must use HTTPS. A Signal
bridge may use HTTP only on a controlled private network. Escalane signs the
state callback with HMAC-SHA256 and places the signature in
`X-Hub-Signature-256`. A generic webhook still needs an exact host allowlist
entry and public-address validation when simulation mode permits HTTP.

Before deployment, test the real credentials, provider field identifiers,
idempotency behavior, and delivery results against each target system. The
local test suite uses controlled substitutes and cannot prove that a live
provider accepts or handles requests correctly.

## Simulation

With `SIMULATION_ENABLED=true`, Escalane replaces live delivery with mocks and
enables the simulation routes. This mode requires a loopback `BASE_URL` and can
exercise the application flow. It does not test provider credentials,
transport, rate limits, idempotency, or actual delivery.

Mock notification records and ticket counters live only in the process that
created them. They disappear on restart, are not shared between separate API
and worker processes, and cannot serve as delivery evidence.
