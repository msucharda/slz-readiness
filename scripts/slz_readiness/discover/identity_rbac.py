"""Discover role assignments at the SLZ management-group scopes.

v1 surfaces presence/count only; deeper RBAC rules land in a later milestone.
Discovery failures produce unknown-severity findings (see az_common.AzError).
"""
from __future__ import annotations

from typing import Any

from .az_common import AzError, az_cmd_str, error_finding, run_az
from .policy_assignments import SCOPES


def discover() -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for mg in SCOPES:
        scope_arg = f"/providers/Microsoft.Management/managementGroups/{mg}"
        args = ["role", "assignment", "list", "--scope", scope_arg]
        try:
            assignments = run_az(args)
        except AzError as err:
            if err.kind == "not_found":
                continue
            findings.append(
                error_finding(
                    "microsoft.authorization/roleassignments",
                    f"scope:mg/{mg}",
                    f"mg/{mg}",
                    args,
                    err,
                )
            )
            continue
        findings.append(
            {
                "resource_type": "microsoft.authorization/roleassignments",
                "resource_id": f"scope:mg/{mg}",
                "scope": f"mg/{mg}",
                "observed_state": [
                    {
                        "roleDefinitionName": a.get("roleDefinitionName"),
                        "principalType": a.get("principalType"),
                    }
                    for a in (assignments or [])
                ],
                "query_cmd": az_cmd_str(args),
            }
        )
    return findings
