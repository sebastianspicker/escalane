---
name: Bug report
about: Something isn't working
labels: bug
---

> **Security reports:** Do not disclose suspected vulnerabilities here. Use the
> private [GitHub Security Advisory form](https://github.com/sebastianspicker/escalane/security/advisories/new)
> instead.

**What happened?**

**What did you expect?**

**Steps to reproduce**

1.
2.
3.

**Affected area**

- [ ] Alarm trigger / Yealink endpoint
- [ ] ACK link UI
- [ ] Admin UI/API
- [ ] Notification connector
- [ ] Escalation / worker job
- [ ] Database / migration
- [ ] Deployment / configuration
- [ ] Documentation
- [ ] Other:

### Environment
- escalane version:
- Python:
- Deployment (Docker Compose / bare metal / other):
- PostgreSQL:
- Redis:

**Configuration context**

- `SIMULATION_ENABLED`:
- Relevant environment variables, with secrets and internal hostnames redacted:

### Impact

- [ ] Crash or startup failure
- [ ] Alarm not created
- [ ] Alarm created but notification/escalation failed
- [ ] Incorrect status shown to user/operator
- [ ] Other:

**Logs / error output**

```
paste here
```

**Verification already tried**

- Commands run:
- Result:
