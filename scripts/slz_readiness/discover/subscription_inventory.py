"""Subscription-inventory discoverer.

Emits one finding per subscription so downstream rules can reason about MG
placement, Defender pricing per subscription, diagnostic-settings per
subscription, etc. v1 does not yet consume this data in a rule — it's surface
coverage that the Tier-3 roadmap builds on.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .az_common import AzError, az_cmd_str, error_finding, run_az


def discover(
    subscription_filter: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    args = ["account", "list", "--all"]
    try:
        subs = run_az(args)
    except AzError as err:
        return [
            error_finding(
                "microsoft.resources/subscriptions",
                "tenant",
                "tenant",
                args,
                err,
            )
        ]
    findings: list[dict[str, Any]] = []
    for sub in subs or []:
        sub_id = sub.get("id") or sub.get("subscriptionId") or ""
        if subscription_filter is not None and sub_id not in subscription_filter:
            continue
        findings.append(
            {
                "resource_type": "microsoft.resources/subscriptions",
                "resource_id": f"subscription/{sub_id}",
                "scope": f"subscription/{sub_id}",
                "observed_state": {
                    "id": sub_id,
                    "displayName": sub.get("name") or sub.get("displayName"),
                    "tenantId": sub.get("tenantId"),
                    "state": sub.get("state"),
                    "isDefault": sub.get("isDefault", False),
                },
                "query_cmd": az_cmd_str(args),
            }
        )
    return findings
