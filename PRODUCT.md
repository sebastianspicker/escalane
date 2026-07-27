# Product scope

Escalane records alarm triggers, coordinates notification and escalation work,
and exposes acknowledgement and operator workflows. It is intended for
technical evaluation and public-alpha development.

## Users

- Operators review alarms, update lifecycle state, add notes, and inspect
  activity.
- Responders acknowledge an alarm through a capability link.
- Administrators configure sites, rooms, people, devices, policies, and
  integrations.
- Maintainers operate the API, worker, PostgreSQL, Redis, and external
  connectors.

## Scope

The project includes device-trigger intake, durable alarm state, asynchronous
notification delivery, delayed escalation, a server-rendered operator console,
a responder acknowledgement page, and operational endpoints.

It does not provide emergency-response staffing, safety certification,
compliance certification, managed hosting, or a guarantee that external
providers will deliver a message.

## Terminology

The product name is Escalane. Python packages, service paths, and technical
identifiers use `escalane`. The Compose image selector is `ESCALANE_IMAGE`.
Alarm lifecycle values are `triggered`, `acknowledged`, `resolved`, and
`cancelled`.

## Interface principles

- Present alarm state, age, person, location, and available action before
  secondary information.
- Keep authorization and lifecycle decisions on the server.
- Require confirmation and a reason for destructive or terminal actions.
- Do not expose static credentials, device tokens, connector destinations, or
  acknowledgement capability tokens in operator output.
- Keep German and English labels equivalent in meaning.
- Preserve keyboard operation, visible focus, text labels for state, reduced
  motion, forced-colour support, and narrow-screen reflow.
