"""Discover Azure Policy assignments at the SLZ management-group scopes."""
from __future__ import annotations

from typing import Any

from .az_common import az_cmd_str, run_az

# Scopes we sweep for policy assignments. Kept narrow to v1 design areas.
SCOPES = [
    "slz",
    "platform",
    "landingzones",
    "corp",
    "online",
    "confidential_corp",
    "confidential_online",
    "identity",
    "management",
]


def discover() -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for mg in SCOPES:
        scope_arg = f"/providers/Microsoft.Management/managementGroups/{mg}"
        args = ["policy", "assignment", "list", "--scope", scope_arg]
        try:
            assignments = run_az(args)
        except Exception:  # noqa: BLE001
            continue  # MG doesn't exist — hierarchy rule will catch that
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
