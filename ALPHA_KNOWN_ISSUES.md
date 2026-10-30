# Escalane alpha – known issues

This alpha is for evaluation. Please read this page before you report a bug.
The issues below are already known.

## Not yet confirmed against real systems

- **Zammad, Signal and SMS delivery.** The requests follow the providers'
  published documentation, but they have not yet been run against a real
  Zammad instance, a real signal-cli REST bridge or the SMS provider. Treat live
  delivery as experimental. Simulation mode is the supported way to evaluate
  Escalane.
- **Email targets** send an email through Zammad, on the alarm's ticket. This
  only works if the Zammad group you configure has an outgoing email address.
- **Signal** needs the phone number of the registered signal-cli account in
  `SIGNAL_SENDER_NUMBER` (international format, for example `+4915100000000`)
  and the group ID that starts with `group.`.
- **Notification texts are in German** (SMS, Signal, email and Zammad notes),
  even when you use the English interface.

## Things that may surprise you

- **Behind a reverse proxy**, set `TRUSTED_PROXY_CIDRS` to your proxy's address.
  Otherwise all users appear to come from the proxy. Five wrong admin keys or
  sign-ins in a minute then block everyone for the rest of that minute, and all
  responders share one acknowledgement limit.
- **The operator console signs you out after 60 minutes without page views.**
  A console that only shows the worklist (for example on a wall screen) reloads
  to the sign-in page when its session ends.
- **The admin key must have at least 32 characters** unless simulation mode is
  on. Generate one with
  `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
- **Simulation records** keep the newest 500 simulated messages until you clear
  them on the simulation page. They can contain acknowledgement links, so clear
  them after a demonstration.
- **Some error pages show a short technical code** (for example
  `reason_required`) or an English detail on a German page. The action itself
  behaves correctly.
- **Escalation steps with a delay of 0 seconds run immediately.** They are not
  disabled. Changing the escalation delays in the environment file affects only
  newly loaded sample data; change existing delays in the console.
- **Only the escalation policy named `default` is used.** Other policies can be
  saved but never trigger.
- **The system page shows Redis as "ok" and has no worker status.** Check the
  worker log when nothing is delivered.

## Workarounds

- If the Compose stack does not start, run every Compose command from the
  repository root with `--env-file .env`, exactly as the README shows.
- If the local `make install` warns about the Python version, continue: any
  Python 3.14 release works, and 3.14.7 is the version CI uses.

## Reporting a bug

Open an issue in the project's GitHub repository and include:

- what you did, what you expected and what happened;
- the Escalane version (shown as "Application" on the console's System page,
  or the image tag) and whether simulation mode was on;
- the **reference code** from the error page, if one was shown;
- the matching log lines. The API and worker write one JSON line per event.
  Collect them with
  `docker compose --env-file .env -f deploy/docker-compose.yml logs api worker`
  and search for the reference code (`request_id`) or the alarm ID;
- your browser and language setting, for console or responder-page problems.

Remove admin keys, device tokens, acknowledgement links, phone numbers and
email addresses from logs and screenshots before you post them. Report
security problems privately as described in the security policy, not in a
public issue.
