"""Collect route modules in the explicit order used to assemble the public API."""

from __future__ import annotations

from escalane.web.routes.ack import router as ack_router
from escalane.web.routes.admin import router as admin_router
from escalane.web.routes.admin_alarms import router as admin_alarms_router
from escalane.web.routes.admin_configuration_import import (
    router as admin_configuration_import_router,
)
from escalane.web.routes.admin_configuration_policy import (
    router as admin_configuration_policy_router,
)
from escalane.web.routes.admin_configuration_resources import (
    router as admin_configuration_resources_router,
)
from escalane.web.routes.admin_ops import router as admin_ops_router
from escalane.web.routes.admin_ui import router as admin_ui_router
from escalane.web.routes.admin_worklist import router as admin_worklist_router
from escalane.web.routes.alarm_notes import router as alarm_notes_router
from escalane.web.routes.alarm_operations import router as alarm_operations_router
from escalane.web.routes.alarms import router as alarms_router
from escalane.web.routes.health import router as health_router
from escalane.web.routes.simulation import router as simulation_router
from escalane.web.routes.yealink import router as yealink_router

ALL_ROUTERS = [
    health_router,
    admin_ui_router,
    admin_worklist_router,
    admin_ops_router,
    admin_alarms_router,
    admin_configuration_policy_router,
    admin_configuration_import_router,
    admin_configuration_resources_router,
    yealink_router,
    ack_router,
    alarms_router,
    alarm_operations_router,
    alarm_notes_router,
    admin_router,
    simulation_router,
]
