# Alarm Broker Documentation

## Overview

This repository implements a release-candidate alarm broker that receives silent alarms, persists alarm state and audit data, fans out notifications, and tracks acknowledgement and escalation workflows.

## Documentation Index

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture, data model, flows, lifecycle |
| [SETUP.md](SETUP.md) | Installation, development setup, configuration reference |
| [OPERATIONS.md](OPERATIONS.md) | Monitoring, backups, performance tuning, troubleshooting |
| [INTEGRATIONS.md](INTEGRATIONS.md) | Yealink/Zammad templates and connector notes |
| [FRONTEND.md](FRONTEND.md) | Browser architecture, UI conventions, and release checks |
| [ROADMAP.md](ROADMAP.md) | Active release-candidate backlog and definition of done |
| [../PRODUCT.md](../PRODUCT.md) | Product purpose, users, principles, and accessibility target |
| [../DESIGN.md](../DESIGN.md) | Shared browser visual and template conventions |

Archived internal planning, audit, and completed-status packets are local-only
working artifacts. They are intentionally excluded from the public documentation
surface and should not be committed. `make hygiene-check` verifies the tracked
and non-ignored repository candidate without reading ignored local workspaces.

## Quick Links

- [Main README](../README.md) - Project overview and quickstart
- [Security Policy](../SECURITY.md) - Security best practices and disclosure process
- [Changelog](../CHANGELOG.md) - Release and change history
