"""Deterministic matchers used by evaluate/engine.py.

A matcher takes `(observed, expected, matcher_spec)` and returns:

    (passed: bool, observed_snapshot: Any)

Matchers never call out to the network, never consult the LLM, and never
consult anything other than their three arguments plus the vendored baseline.
"""
from __future__ import annotations

from typing import Any, Callable

from .loaders import read_baseline_json
from .models import BaselineRef

Matcher = Callable[[Any, Any, dict[str, Any]], tuple[bool, Any]]


def _get_path(obj: Any, dotted: str) -> Any:
    cur: Any = obj
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def equals(observed: Any, expected: Any, spec: dict[str, Any]) -> tuple[bool, Any]:
    """observed == expected after normalising both to JSON-comparable form."""
    if "path" in spec:
        observed = _get_path(observed, spec["path"])
    return observed == expected, observed


def contains_all(observed: Any, expected: Any, spec: dict[str, Any]) -> tuple[bool, Any]:
    """observed list contains every item in expected list (order-insensitive)."""
    if "path" in spec:
        observed = _get_path(observed, spec["path"])
    obs = set(observed or [])
    exp = set(expected or [])
    missing = list(exp - obs)
    return not missing, {"present": sorted(obs & exp), "missing": sorted(missing)}


def policy_assignments_include(
    observed: Any, expected: Any, spec: dict[str, Any]
) -> tuple[bool, Any]:
    """Tenant scope must have each policy assignment named in `expected`.

    `observed` is the list of policy-assignment objects from
    `az policy assignment list`. Matches on `name` (policyAssignmentName).
    """
    names = {item.get("name") for item in (observed or [])}
    exp_names = set(expected or [])
    missing = sorted(exp_names - names)
    return not missing, {"present": sorted(names & exp_names), "missing": missing}


def archetype_policies_applied(
    observed: Any, expected: Any, spec: dict[str, Any]
) -> tuple[bool, Any]:
    """Read the archetype JSON from the baseline and verify each of its
    `policy_assignments` exists on the tenant."""
    baseline_ref = BaselineRef(**spec["archetype_ref"])
    archetype = read_baseline_json(baseline_ref)
    required: list[str] = archetype.get("policy_assignments", [])
    obs_names = {item.get("name") for item in (observed or [])}
    missing = sorted(set(required) - obs_names)
    return not missing, {"required": required, "missing": missing}


def any_subscription_has_workspace(
    observed: Any, expected: Any, spec: dict[str, Any]
) -> tuple[bool, Any]:
    """Passes when at least one subscription in the tenant has a Log Analytics
    workspace. ``observed`` is either a single per-subscription observation
    dict (``{"workspaces": [...]}``) or, with aggregate=tenant, a list of
    such dicts — one per subscription.
    """
    items = observed if isinstance(observed, list) else [observed or {}]
    workspaces: list[Any] = []
    for item in items:
        workspaces.extend((item or {}).get("workspaces", []) or [])
    return len(workspaces) >= 1, {
        "workspace_count": len(workspaces),
        # Keep the snapshot small so gaps.json stays readable.
        "workspaces_sample": workspaces[:5],
    }


MATCHERS: dict[str, Matcher] = {
    "equals": equals,
    "contains_all": contains_all,
    "policy_assignments_include": policy_assignments_include,
    "archetype_policies_applied": archetype_policies_applied,
    "any_subscription_has_workspace": any_subscription_has_workspace,
}


def get_matcher(name: str) -> Matcher:
    if name not in MATCHERS:
        raise KeyError(f"Unknown matcher '{name}'. Known: {sorted(MATCHERS)}")
    return MATCHERS[name]
