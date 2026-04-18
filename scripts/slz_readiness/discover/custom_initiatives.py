"""Discover custom ``policySetDefinitions`` (initiatives) at SLZ MG scopes.

Rung-D gap detection relies on this: when an operator deploys a custom
initiative at a landing-zone MG and assigns *it* instead of the canonical
SLZ initiative, the equivalence matcher compares the custom initiative's
``policyDefinitions`` set against the vendored baseline's set. Without
these findings, drift is invisible.

Read-only: uses ``az policy set-definition list`` with a
managementGroup-scoped query. Skips built-in definitions (``policyType
!= "Custom"``) — only operator-authored initiatives are relevant.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import _trace
from ._alias import load_aliased_mgs
from .az_common import AzError, az_cmd_str, error_finding, run_az

# Same scope list as policy_assignments.py. Custom initiatives can live at
# any MG but, in practice, operators place them near the workload they
# govern (landing zones, corp, online). Sweep the full tree so we catch
# anything at the root too.
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
    """Same semantics as ``policy_assignments._probe_targets`` — canonical
    SLZ names ∩ ``present``, then non-null brownfield aliases appended in
    stable order."""
    targets: list[str] = [mg for mg in SCOPES if mg in present]
    aliased = load_aliased_mgs(run_dir)
    seen = set(targets)
    for mg in aliased:
        if mg in present and mg not in seen:
            targets.append(mg)
            seen.add(mg)
            _trace.log("discover.extra_mg_probed", module="custom_initiatives", mg=mg)
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
        args = [
            "policy", "set-definition", "list",
            "--management-group", mg,
            "--query", "[?policyType=='Custom']",
        ]
        try:
            initiatives = run_az(args)
        except AzError as err:
            if err.kind == "not_found":
                continue
            findings.append(
                error_finding(
                    "microsoft.authorization/policysetdefinitions",
                    f"scope:mg/{mg}",
                    f"mg/{mg}",
                    args,
                    err,
                )
            )
            continue
        findings.append(
            {
                "resource_type": "microsoft.authorization/policysetdefinitions",
                "resource_id": f"scope:mg/{mg}",
                "scope": f"mg/{mg}",
                "observed_state": [
                    {
                        "id": d.get("id"),
                        "name": d.get("name"),
                        "displayName": d.get("displayName"),
                        "policyType": d.get("policyType"),
                        # policyDefinitions is the list we compare for
                        # equivalence in the rung-D matcher.
                        "policyDefinitions": d.get("policyDefinitions") or [],
                    }
                    for d in (initiatives or [])
                ],
                "query_cmd": az_cmd_str(args),
            }
        )
        # Keep scope_arg referenced so static checkers don't warn; the az
        # CLI invocation above uses ``--management-group`` directly.
        del scope_arg
    return findings
