"""Discover Log Analytics workspaces across the tenant.

Emits one finding per subscription observed: even subscriptions with zero
workspaces get a finding with empty ``observed_state.workspaces``, so the
``logging.management_la_workspace_exists`` rule can select by subscription.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from .az_common import AzError, az_cmd_str, error_finding, run_az


def discover(
    subscription_filter: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    args = [
        "graph", "query", "--graph-query",
        "resources | where type =~ 'microsoft.operationalinsights/workspaces' "
        "| project name, id, resourceGroup, location, subscriptionId",
    ]
    if subscription_filter:
        # Scope the ARM graph call itself — cheaper and avoids cross-sub read
        # attempts that would just error out as permission_denied.
        args.extend(["--subscriptions", *sorted(subscription_filter)])
    try:
        result = run_az(args)
    except AzError as err:
        return [
            error_finding(
                "microsoft.operationalinsights/workspaces",
                "tenant",
                "tenant",
                args,
                err,
            )
        ]
    rows = result.get("data", []) if isinstance(result, dict) else []
    by_sub: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_sub[row.get("subscriptionId") or "unknown"].append(row)
    findings: list[dict[str, Any]] = [
        {
            "resource_type": "microsoft.operationalinsights/workspaces",
            "resource_id": f"subscription/{sub_id}",
            "scope": f"subscription/{sub_id}",
            "observed_state": {"workspaces": workspaces},
            "query_cmd": az_cmd_str(args),
        }
        for sub_id, workspaces in sorted(by_sub.items())
    ]
    if not findings:
        # Still emit one tenant-level finding so the rule sees the query ran.
        findings.append(
            {
                "resource_type": "microsoft.operationalinsights/workspaces",
                "resource_id": "tenant",
                "scope": "tenant",
                "observed_state": {"workspaces": []},
                "query_cmd": az_cmd_str(args),
            }
        )
    return findings
