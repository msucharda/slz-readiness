"""Template registry — maps evaluate rule_ids to scaffold templates.

This is the ONLY place where a gap is allowed to pick a Bicep template.
The mapping is deterministic and reviewed at PR time.
"""
from __future__ import annotations

# rule_id -> template stem (matches files under scripts/scaffold/avm_templates/<stem>.bicep)
RULE_TO_TEMPLATE: dict[str, str] = {
    "mg.slz.hierarchy_shape": "management-groups",
    "identity.platform_identity_mg_exists": "management-groups",
    "logging.management_mg_exists": "management-groups",
    "policy.slz.sovereign_root_policies_applied": "sovereignty-policies",
    "sovereignty.confidential_corp_policies_applied": "sovereignty-policies",
    "sovereignty.confidential_online_policies_applied": "sovereignty-policies",
}

# Templates shipped in the plugin. The scaffold engine refuses to emit
# anything not in this list.
ALLOWED_TEMPLATES = {
    "management-groups",
    "policy-assignment",
    "sovereignty-policies",
    "log-analytics",
    "role-assignment",
}
