"""Discover Log Analytics workspaces across the tenant (v1 MVP)."""
from __future__ import annotations

from typing import Any

from .az_common import az_cmd_str, run_az


def discover() -> list[dict[str, Any]]:
    args = [
        "graph", "query", "--graph-query",
        "resources | where type =~ 'microsoft.operationalinsights/workspaces' | project name, id, resourceGroup, location",
    ]
    try:
        rows = run_az(args).get("data", [])
    except Exception:  # noqa: BLE001
        rows = []
    return [
        {
            "resource_type": "microsoft.operationalinsights/workspaces",
            "resource_id": row.get("id", ""),
            "scope": row.get("resourceGroup", ""),
            "observed_state": row,
            "query_cmd": az_cmd_str(args),
        }
        for row in rows
    ]
