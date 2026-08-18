"""Stable router facade for the administrative configuration console.

The public router and legacy helper imports remain here while cohesive route
workflows live in dedicated policy, import, and master-data modules.
"""

from __future__ import annotations

from fastapi import APIRouter

from escalane.api.routes.admin_configuration_import import (
    admin_import_page,
    admin_import_submit,
    apply_seed_payload,
    parse_seed_payload,
)
from escalane.api.routes.admin_configuration_import import (
    router as import_router,
)
from escalane.api.routes.admin_configuration_policy import (
    ConfigurationPolicyJson,
    _policy_payload,
    _retain_masked_target_addresses,
    admin_escalation_page,
    admin_escalation_save,
    apply_escalation_policy,
)
from escalane.api.routes.admin_configuration_policy import (
    router as policy_router,
)
from escalane.api.routes.admin_configuration_resources import (
    _REQUIRED_FIELDS,
    _RESOURCE_FIELDS,
    _RESOURCE_MODELS,
    _RETAIN_EXISTING,
    ConfigurationCsrfToken,
    ConfigurationMutationContext,
    ConfigurationOptionalVersion,
    ConfigurationSessionCookie,
    ConfigurationVersion,
    _active_dependency_counts,
    _commit_resource_mutation,
    _configuration_mutation_context,
    _create_resource,
    _deactivate_resource_if_current,
    _delete_resource_if_current,
    _device_token_value,
    _historical_dependency_count,
    _lock_resource_for_mutation,
    _mutation_applied,
    _raise_version_conflict,
    _resource_field_value,
    _resource_form_values,
    _resource_model,
    _resource_row,
    _update_resource_if_current,
    admin_configuration_deactivate,
    admin_configuration_delete,
    admin_configuration_list,
    admin_configuration_save,
    lock_active_referenced_parents,
    set_saved_flash,
)
from escalane.api.routes.admin_configuration_resources import (
    router as resources_router,
)
from escalane.services.admin_audit import add_admin_audit_event

router = APIRouter()

# Preserve the original registration order: exact policy and import routes must
# win before the generic resource-page route is considered.
router.include_router(policy_router)
router.include_router(import_router)
router.include_router(resources_router)

__all__ = [
    "ConfigurationCsrfToken",
    "ConfigurationMutationContext",
    "ConfigurationOptionalVersion",
    "ConfigurationPolicyJson",
    "ConfigurationSessionCookie",
    "ConfigurationVersion",
    "_RESOURCE_FIELDS",
    "_RESOURCE_MODELS",
    "_REQUIRED_FIELDS",
    "_RETAIN_EXISTING",
    "_active_dependency_counts",
    "_commit_resource_mutation",
    "_configuration_mutation_context",
    "_create_resource",
    "_deactivate_resource_if_current",
    "_delete_resource_if_current",
    "_device_token_value",
    "_historical_dependency_count",
    "_lock_resource_for_mutation",
    "_mutation_applied",
    "_policy_payload",
    "_raise_version_conflict",
    "_resource_field_value",
    "_resource_form_values",
    "_resource_model",
    "_resource_row",
    "_retain_masked_target_addresses",
    "_update_resource_if_current",
    "admin_configuration_deactivate",
    "admin_configuration_delete",
    "admin_configuration_list",
    "admin_configuration_save",
    "admin_escalation_page",
    "admin_escalation_save",
    "admin_import_page",
    "admin_import_submit",
    "add_admin_audit_event",
    "apply_escalation_policy",
    "apply_seed_payload",
    "lock_active_referenced_parents",
    "parse_seed_payload",
    "router",
    "set_saved_flash",
]
