---
name: Bug report
about: Something isn't working
labels: bug
---

For a suspected vulnerability, follow the
[security policy](https://github.com/sebastianspicker/escalane/security/policy)
instead of describing it in a public issue.

## What happened?

Describe the problem and what you expected to happen.

## How can we reproduce it?

Include the steps and commands needed to reproduce the problem with fictional
data. If you have already tried a fix or a check, tell us what happened.

1.
2.
3.

## Where does it happen?

- [ ] Alarm trigger / Yealink endpoint
- [ ] Acknowledgement link
- [ ] Admin UI or API
- [ ] Notification provider
- [ ] Escalation or worker job
- [ ] Database or migration
- [ ] Deployment or configuration
- [ ] Documentation
- [ ] Other:

## Environment

- Escalane version, tag, or commit:
- Python version:
- Deployment method (Docker Compose, local processes, or other):
- PostgreSQL version:
- Redis version:
- `SIMULATION_ENABLED`:
- Other relevant configuration, with secrets and internal hostnames removed:

## Impact

For example: does startup fail, is an alarm missing, does a notification fail,
or does the interface show the wrong status?

## Logs or error output

Include only the output needed to explain the problem. Remove credentials,
tokens, acknowledgement URLs, personal alarm data, and internal hostnames
before posting it.

```text
Paste redacted output here.
```
