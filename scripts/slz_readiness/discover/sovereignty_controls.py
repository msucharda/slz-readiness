"""Discover sovereignty controls via Azure Policy compliance state.

Reads policy-state non-compliance for the Microsoft Cloud for Sovereignty
assignments (``Enforce-Sovereign-Global`` / ``Enforce-Sovereign-Conf``) across
every subscription discovered. Surfaces resources that violate sovereignty
guardrails even when the assignment itself is present — which
``archetype_policies_applied`` rules can't detect.

v1 of this expanded module is data-only. No rule in v0.2.0 consumes these
findings yet — the ``unknown`` path is wired so the plan phase can render
them if/when a Tier-3 rule lands.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .az_common import AzError, az_cmd_str, error_finding, run_az

SOVEREIGN_ASSIGNMENTS = ("Enforce-Sovereign-Global", "Enforce-Sovereign-Conf")


def discover(
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
    subscription_filter: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    try:
        subs = run_az(["account", "list", "--all"])
    except AzError:
        # subscription_inventory emits a detailed error_finding for this.
        return []
    findings: list[dict[str, Any]] = []
    sub_list = [s for s in (subs or []) if (s.get("id") or s.get("subscriptionId"))]
    if subscription_filter is not None:
        sub_list = [
            s for s in sub_list
            if (s.get("id") or s.get("subscriptionId")) in subscription_filter
        ]
    total = len(sub_list) * len(SOVEREIGN_ASSIGNMENTS)
    i = 0
    for sub in sub_list:
        sub_id = sub.get("id") or sub.get("subscriptionId") or ""
        for assignment in SOVEREIGN_ASSIGNMENTS:
            i += 1
            if progress_cb is not None:
                progress_cb(f"sub={sub_id} assignment={assignment}", i, total)
            args = [
                "policy", "state", "list",
                "--subscription", sub_id,
                "--filter",
                f"PolicyAssignmentName eq '{assignment}' and complianceState eq 'NonCompliant'",
            ]
            try:
                rows = run_az(args)
            except AzError as err:
                findings.append(
                    error_finding(
                        "microsoft.policyinsights/policystates",
                        f"subscription/{sub_id}/assignment/{assignment}",
                        f"subscription/{sub_id}",
                        args,
                        err,
                    )
                )
                continue
            findings.append(
                {
                    "resource_type": "microsoft.policyinsights/policystates",
                    "resource_id": f"subscription/{sub_id}/assignment/{assignment}",
                    "scope": f"subscription/{sub_id}",
                    "observed_state": {
                        "assignmentName": assignment,
                        "nonCompliantCount": len(rows or []),
                    },
                    "query_cmd": az_cmd_str(args),
                }
            )
    return findings
