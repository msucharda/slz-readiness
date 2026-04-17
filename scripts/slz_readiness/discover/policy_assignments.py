"""Discover Azure Policy assignments at the SLZ management-group scopes."""
from __future__ import annotations

from typing import Any

from .az_common import AzError, az_cmd_str, error_finding, run_az

# Scopes we sweep for policy assignments. Covers every archetype-bearing MG
# in the SLZ hierarchy so the archetype_policies_applied rules have data.
SCOPES = [
    "slz",
    "platform",
    "landingzones",
    "corp",
    "online",
    "confidential_corp",
    "confidential_online",
    "public",
    "identity",
    "management",
    "connectivity",
    "security",
    "sandbox",
    "decommissioned",
]


def discover() -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for mg in SCOPES:
        scope_arg = f"/providers/Microsoft.Management/managementGroups/{mg}"
        args = ["policy", "assignment", "list", "--scope", scope_arg]
        try:
            assignments = run_az(args)
        except AzError as err:
            if err.kind == "not_found":
                # MG genuinely absent — mg.slz.hierarchy_shape rule covers this.
                # No need to emit noise per archetype rule.
                continue
            # permission_denied / rate_limited / network — surface as unknown.
            findings.append(
                error_finding(
                    "microsoft.authorization/policyassignments",
                    f"scope:mg/{mg}",
                    f"mg/{mg}",
                    args,
                    err,
                )
            )
            continue
        findings.append(
            {
                "resource_type": "microsoft.authorization/policyassignments",
                "resource_id": f"scope:mg/{mg}",
                "scope": f"mg/{mg}",
                "observed_state": [
                    {"name": a.get("name"), "displayName": a.get("displayName")}
                    for a in (assignments or [])
                ],
                "query_cmd": az_cmd_str(args),
            }
        )
    return findings
