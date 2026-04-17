"""Discover role assignments at the SLZ management-group scopes.

v1 surfaces presence/count only; deeper RBAC rules land in a later milestone.
Discovery failures produce unknown-severity findings (see az_common.AzError).
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .az_common import AzError, az_cmd_str, error_finding, run_az
from .policy_assignments import SCOPES


def discover(progress_cb: Optional[Callable[[str, int, int], None]] = None) -> list[dict[str, Any]]:
    from .mg_hierarchy import present_mg_ids

    present = set(present_mg_ids())
    targets = [mg for mg in SCOPES if mg in present]
    findings: list[dict[str, Any]] = []
    for i, mg in enumerate(targets, start=1):
        if progress_cb is not None:
            progress_cb(f"mg={mg}", i, len(targets))
        scope_arg = f"/providers/Microsoft.Management/managementGroups/{mg}"
        args = [
            "role", "assignment", "list",
            "--scope", scope_arg,
            "--fill-principal-name", "false",
            "--fill-role-definition-name", "false",
        ]
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
                        "roleDefinitionId": a.get("roleDefinitionId"),
                        "principalType": a.get("principalType"),
                    }
                    for a in (assignments or [])
                ],
                "query_cmd": az_cmd_str(args),
            }
        )
    return findings
