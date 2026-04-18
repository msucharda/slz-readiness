"""Discover Azure Policy assignments at the SLZ management-group scopes."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import _trace
from ._alias import load_aliased_mgs
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


def _probe_targets(present: set[str], run_dir: Path | None = None) -> list[str]:
    """Return the MG list to sweep — canonical SLZ names ∪ brownfield aliases.

    Greenfield (no ``mg_alias.json``): returns ``[mg for mg in SCOPES if mg in present]``,
    byte-identical to pre-v0.7.0 behaviour.

    Brownfield: union with non-null customer MG names from the alias file.
    Each alias is intersected against ``present`` so we never probe a name
    the tenant doesn't have. Duplicates removed; canonical order preserved
    for the SLZ names, then aliased names appended in sorted order so
    ``trace.jsonl`` is stable.
    """
    targets: list[str] = [mg for mg in SCOPES if mg in present]
    aliased = load_aliased_mgs(run_dir)
    seen = set(targets)
    for mg in aliased:
        if mg in present and mg not in seen:
            targets.append(mg)
            seen.add(mg)
            _trace.log("discover.extra_mg_probed", module="policy_assignments", mg=mg)
    return targets


def discover(
    progress_cb: Callable[[str, int, int], None] | None = None,
    run_dir: Path | None = None,
) -> list[dict[str, Any]]:
    from .mg_hierarchy import present_mg_ids

    present = set(present_mg_ids())
    targets = _probe_targets(present, run_dir=run_dir)
    findings: list[dict[str, Any]] = []
    for i, mg in enumerate(targets, start=1):
        if progress_cb is not None:
            progress_cb(f"mg={mg}", i, len(targets))
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
                    {
                        "name": a.get("name"),
                        "displayName": a.get("displayName"),
                        # v0.7.0: capture identity fields needed by rung-B
                        # equivalence (renamed assignments) and rung-C
                        # parameter drift. These are already returned by
                        # ``az policy assignment list`` — just keep them.
                        "policyDefinitionId": a.get("policyDefinitionId"),
                        "enforcementMode": a.get("enforcementMode"),
                        "notScopes": a.get("notScopes") or [],
                    }
                    for a in (assignments or [])
                ],
                "query_cmd": az_cmd_str(args),
            }
        )
    return findings
