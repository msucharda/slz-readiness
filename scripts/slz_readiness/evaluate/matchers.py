"""Deterministic matchers used by evaluate/engine.py.

A matcher takes `(observed, expected, matcher_spec)` and returns:

    (passed: bool, observed_snapshot: Any)

Matchers never call out to the network, never consult the LLM, and never
consult anything other than their three arguments plus the vendored baseline.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .. import _trace
from .loaders import RuleLoadError, read_baseline_json
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


def _load_required_defids(
    archetype_ref: BaselineRef, required: list[str]
) -> dict[str, str]:
    """Map ``required`` assignment names → ``properties.policyDefinitionId``.

    Per-assignment JSONs live in the same subtree as the archetype JSON,
    under ``policy_assignments/<name>.alz_policy_assignment.json``. Mirrors
    the convention enforced by ``scaffold/engine._SUBTREE_FOR_ARCHETYPE_RULE``.

    Failures (missing file, missing field) silently omit the entry — the
    caller falls back to name-only matching for that assignment, preserving
    pre-v0.7.0 semantics on a partially-vendored baseline.
    """
    # archetype_ref.path looks like "platform/alz/archetype_definitions/<name>.alz_archetype_definition.json".
    # Subtree = the two leading components ("platform/alz").
    parts = archetype_ref.path.split("/")
    if len(parts) < 4 or parts[-2] != "archetype_definitions":
        return {}
    subtree = "/".join(parts[:-2])
    out: dict[str, str] = {}
    for name in required:
        ref = BaselineRef(
            source=archetype_ref.source,
            path=f"{subtree}/policy_assignments/{name}.alz_policy_assignment.json",
            sha=archetype_ref.sha,
        )
        try:
            doc = read_baseline_json(ref)
        except (FileNotFoundError, OSError, json.JSONDecodeError, KeyError, RuleLoadError) as exc:
            # Tolerate a partially-vendored baseline (the rule still works
            # at name-only granularity); record why we skipped this assignment
            # so audit trails can show that defid matching was downgraded.
            _trace.log(
                "evaluate.defid_load_skip",
                assignment=name,
                ref=ref.path,
                reason=f"{type(exc).__name__}: {exc}",
            )
            continue
        defid = ((doc or {}).get("properties") or {}).get("policyDefinitionId")
        if isinstance(defid, str) and defid:
            out[name] = defid
    return out


def archetype_policies_applied(
    observed: Any, expected: Any, spec: dict[str, Any]
) -> tuple[bool, Any]:
    """Read the archetype JSON from the baseline and verify each of its
    `policy_assignments` exists on the tenant.

    v0.7.0 — rung-B equivalence: when a required assignment ``name`` is
    not found, also check whether any observed assignment carries the same
    ``policyDefinitionId``. This catches the brownfield case where the
    operator deployed the same Microsoft built-in policy under a different
    assignment name. Pure function — extra data comes from the same
    vendored baseline files.
    """
    baseline_ref = BaselineRef(**spec["archetype_ref"])
    archetype = read_baseline_json(baseline_ref)
    required: list[str] = archetype.get("policy_assignments", [])
    obs_list = list(observed or [])
    obs_names = {item.get("name") for item in obs_list}
    obs_defids = {
        item.get("policyDefinitionId")
        for item in obs_list
        if isinstance(item.get("policyDefinitionId"), str)
    }

    present_by_name = sorted(set(required) & obs_names)
    initially_missing = sorted(set(required) - obs_names)

    matched_by_defid: list[dict[str, str]] = []
    still_missing: list[str] = []
    if initially_missing:
        defids = _load_required_defids(baseline_ref, initially_missing)
        for name in initially_missing:
            defid = defids.get(name)
            if defid and defid in obs_defids:
                # Find the observed assignment name that supplies the match
                # so the gap snapshot points at the actual aliased deployment.
                observed_name = next(
                    (
                        o.get("name")
                        for o in obs_list
                        if o.get("policyDefinitionId") == defid
                    ),
                    None,
                )
                matched_by_defid.append(
                    {
                        "required_name": name,
                        "observed_name": observed_name or "",
                        "policy_definition_id": defid,
                    }
                )
                _trace.log(
                    "evaluate.definition_id_match",
                    required_name=name,
                    observed_name=observed_name,
                    policy_definition_id=defid,
                )
            else:
                still_missing.append(name)

    snapshot: dict[str, Any] = {
        "required": required,
        "present": present_by_name,
        "missing": still_missing,
        "matched_by_defid": matched_by_defid,
    }
    return not still_missing, snapshot


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
