"""Template registry — maps evaluate rule_ids to scaffold templates.

This is the CANONICAL source of truth for which gap emits which Bicep. The
``remediation_template`` field in rule YAMLs is advisory — ``scripts/evaluate/rules/``
is kept in sync by the ``test_registry_matches_yaml`` CI test.

Rationale: the registry sits next to the scaffold engine (same module), so a
reviewer changing the emit surface only has to look in one place. Rule YAMLs
focus on the detection side.
"""
from __future__ import annotations

# rule_id -> template stem (matches files under scripts/scaffold/avm_templates/<stem>.bicep)
#
# Grouping convention (v0.2.0):
#   * MG existence / shape rules -> management-groups
#   * Sovereign-root policies     -> sovereignty-global-policies  (targetScope mg/slz)
#   * Confidential archetypes     -> sovereignty-confidential-policies (targetScope mg/confidential_*)
#   * ALZ / SLZ archetype policy coverage -> archetype-policies (loop over per-MG set)
#   * Logging workspace           -> log-analytics
RULE_TO_TEMPLATE: dict[str, str] = {
    # Management-group hierarchy
    "mg.slz.hierarchy_shape": "management-groups",
    "identity.platform_identity_mg_exists": "management-groups",
    "logging.management_mg_exists": "management-groups",

    # Sovereignty (split: Global at slz root, Confidential at each confidential_* MG)
    "policy.slz.sovereign_root_policies_applied": "sovereignty-global-policies",
    "sovereignty.confidential_corp_policies_applied": "sovereignty-confidential-policies",
    "sovereignty.confidential_online_policies_applied": "sovereignty-confidential-policies",

    # Archetype policy coverage (one policy-assignments deployment per MG scope)
    "archetype.alz_connectivity_policies_applied": "archetype-policies",
    "archetype.alz_corp_policies_applied": "archetype-policies",
    "archetype.alz_decommissioned_policies_applied": "archetype-policies",
    "archetype.alz_identity_policies_applied": "archetype-policies",
    "archetype.alz_landing_zones_policies_applied": "archetype-policies",
    "archetype.alz_platform_policies_applied": "archetype-policies",
    "archetype.alz_sandbox_policies_applied": "archetype-policies",
    "archetype.slz_public_policies_applied": "archetype-policies",

    # Logging
    "logging.management_la_workspace_exists": "log-analytics",
}

# Templates shipped in the plugin. The scaffold engine refuses to emit
# anything not in this list.
ALLOWED_TEMPLATES = {
    "management-groups",
    "policy-assignment",
    "sovereignty-global-policies",
    "sovereignty-confidential-policies",
    "archetype-policies",
    "log-analytics",
    "role-assignment",
}

# ARM deployment scope for each template. Must stay in lockstep with the
# ``targetScope`` line of each ``.bicep`` under scripts/scaffold/avm_templates/
# — ARM rejects any mismatch between the template's targetScope and the
# ``az deployment <scope>`` command the scaffold emits. The CLI layer reads
# this map to pick the right ``az deployment mg|group|tenant`` verb and flag
# set (``--location`` for mg/tenant, ``--resource-group`` for rg).
TEMPLATE_SCOPES: dict[str, str] = {
    "management-groups": "managementGroup",
    "policy-assignment": "managementGroup",
    "sovereignty-global-policies": "managementGroup",
    "sovereignty-confidential-policies": "managementGroup",
    "archetype-policies": "managementGroup",
    "role-assignment": "managementGroup",
    # Subscription-scope: the template creates its own resource group
    # (`Microsoft.Resources/resourceGroups`) so operators don't have to
    # pre-provision it on a fresh subscription. Deployed via
    # `az deployment sub create --location <region>` — not `az deployment group`.
    "log-analytics": "subscription",
}

# Runbooks emitted alongside Bicep for operators without tenant-scope deploy
# rights. Each runbook keys off a Bicep template and is written to
# ``out_dir/runbooks/<runbook>``. Follows the same allowlist discipline as
# ALLOWED_TEMPLATES (rule 5 analogue): the scaffold engine refuses to emit
# anything not in this map.
#
# Shape: ``template_stem -> list[runbook_filename]``. Every filename must
# exist under ``scripts/scaffold/runbooks/``.
TEMPLATE_RUNBOOKS: dict[str, list[str]] = {
    "management-groups": [
        "deploy-mg-hierarchy-lowpriv.ps1",
        "deploy-mg-hierarchy-lowpriv.sh",
    ],
}

ALLOWED_RUNBOOKS: set[str] = {
    rb for rbs in TEMPLATE_RUNBOOKS.values() for rb in rbs
}
