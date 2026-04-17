"""Discover Azure management-group hierarchy."""
from __future__ import annotations

from typing import Any

from .az_common import az_cmd_str, run_az


def discover() -> list[dict[str, Any]]:
    args = ["account", "management-group", "list", "--no-register"]
    try:
        mgs = run_az(args)
    except Exception:  # noqa: BLE001 - tolerate empty tenants / permission errors
        mgs = []
    present_ids = sorted({mg.get("name") for mg in (mgs or []) if mg.get("name")})
    return [
        {
            "resource_type": "microsoft.management/managementgroups.summary",
            "resource_id": "tenant",
            "scope": "/",
            "observed_state": {"present_ids": present_ids},
            "query_cmd": az_cmd_str(args),
        }
    ]
