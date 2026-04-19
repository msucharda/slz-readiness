"""Deterministic pre-fill for Scaffold parameters.

Given discover ``findings`` + evaluate ``gaps`` + ``run_scope``, emit a
``{<template-stem>: {<key>: <value>, ...}}`` map of sensible defaults.

Design invariants:

* **Pure function.** No I/O, no LLM calls, no environment reads. Same
  inputs → byte-identical output (enforced by golden test). Used before
  :func:`slz_readiness.scaffold.engine.scaffold_for_gaps` to seed user
  params.
* **Conservative.** Only emit a key when we can derive it from
  ``findings``. Never fabricate (e.g. never invent a workspaceName).
  Missing keys fall through to schema defaults or user input.
* **Engine-owned fields are NOT pre-filled.** ``archetype-policies.assignments``
  is written from baseline by the engine — :func:`prefill_params` never
  sets it and :func:`strip_engine_owned_fields` strips it from operator
  input before merge.
* **Top-level keyed merge.** Operator-supplied params override prefilled
  values at the top-level key level (per template stem). See
  :func:`merge_params`.

This module is the home of the "pre-assessment values" UX guarantee from
the research report: the operator sees sensible defaults derived from
their actual tenant, not ALZ placeholders.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

# Fields the Scaffold engine populates from baseline — operators MAY NOT
# override these. If they try, the CLI strips the key and emits a warning.
_ENGINE_OWNED_FIELDS: dict[str, tuple[str, ...]] = {
    "archetype-policies": ("assignments",),
}


def _workspaces_from_findings(findings: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten ``observed_state.workspaces`` across logging-monitoring findings.

    Sorted by ``(subscriptionId, id)`` so repeated calls over permuted
    finding order return the same first-row.
    """
    flat: list[dict[str, Any]] = []
    for f in findings:
        if f.get("resource_type") != "microsoft.operationalinsights/workspaces":
            continue
        ws = (f.get("observed_state") or {}).get("workspaces") or []
        for w in ws:
            if isinstance(w, dict):
                flat.append(w)
    flat.sort(key=lambda w: (str(w.get("subscriptionId", "")), str(w.get("id", ""))))
    return flat


def _modal_region(findings: Iterable[dict[str, Any]]) -> str | None:
    """Return the most common ``location`` observed across workspace findings.

    Tiebreak: alphabetical ascending (determinism). Returns ``None`` when
    no workspace has a location — prefill omits the key and the schema
    default / operator input takes over.
    """
    locs: list[str] = []
    for w in _workspaces_from_findings(findings):
        loc = w.get("location")
        if isinstance(loc, str) and loc:
            locs.append(loc)
    if not locs:
        return None
    counts = Counter(locs)
    max_count = max(counts.values())
    tied = sorted(k for k, v in counts.items() if v == max_count)
    return tied[0]


def _slz_parent_id(
    findings: list[dict[str, Any]],
    alias_map: dict[str, str] | None,
) -> str | None:
    """Resolve the *actual* parent MG id of the SLZ root.

    Brownfield invariant (slz-demo run 20260419T120215Z, finding H2):
    the SLZ root MG is often a *child* of an intermediate MG (e.g.
    ``sucharda``) rather than the tenant root. Defaulting
    ``parentManagementGroupId`` to the tenant root (the pre-H2
    behaviour) silently re-parents the SLZ root on first deploy,
    discarding the intermediate MG.

    Resolution order:
    1. If ``alias_map["slz"]`` is set, look up that MG's ``parent_id``
       in ``observed_state.present_details`` from the management-group
       summary finding.
    2. Otherwise return ``None`` — the caller MUST treat the field as
       unknown and emit a placeholder/TODO sentinel rather than
       defaulting to tenant root.
    """
    target = (alias_map or {}).get("slz")
    if not target:
        return None
    for f in findings:
        if f.get("resource_type") != "microsoft.management/managementgroups.summary":
            continue
        details = (f.get("observed_state") or {}).get("present_details") or []
        for d in details:
            if not isinstance(d, dict):
                continue
            if d.get("id") == target:
                parent = d.get("parent_id")
                if isinstance(parent, str) and parent:
                    return parent
    return None


def prefill_params(
    findings: list[dict[str, Any]],
    gaps: list[dict[str, Any]],  # noqa: ARG001 — reserved for future rule-aware fills
    run_scope: dict[str, Any] | None,
    *,
    alias_map: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Derive scaffold parameters from discover findings + run_scope.

    See module docstring for invariants.
    """
    out: dict[str, dict[str, Any]] = {}
    run_scope = run_scope or {}

    # management-groups ---------------------------------------------------
    # H2: prefer the OBSERVED parent of the aliased SLZ root over a blind
    # tenant_id default. Falls through to tenant_id only when no alias
    # is set (greenfield) so existing single-tenant flows still work.
    slz_parent = _slz_parent_id(findings, alias_map)
    tenant_id = run_scope.get("tenant_id")
    if isinstance(slz_parent, str) and slz_parent:
        out["management-groups"] = {"parentManagementGroupId": slz_parent}
    elif isinstance(tenant_id, str) and tenant_id:
        out["management-groups"] = {"parentManagementGroupId": tenant_id}

    # log-analytics -------------------------------------------------------
    workspaces = _workspaces_from_findings(findings)
    if workspaces:
        first = workspaces[0]
        la: dict[str, Any] = {}
        name = first.get("name")
        if isinstance(name, str) and name:
            la["workspaceName"] = name
        loc = first.get("location")
        if isinstance(loc, str) and loc:
            la["location"] = loc
        rg = first.get("resourceGroup")
        if isinstance(rg, str) and rg:
            la["resourceGroupName"] = rg
        if la:
            out["log-analytics"] = la

    # archetype-policies.identityLocation + sovereignty-global-policies.listOfAllowedLocations
    modal = _modal_region(findings)
    if modal:
        out["archetype-policies"] = {"identityLocation": modal}
        out["sovereignty-global-policies"] = {"listOfAllowedLocations": [modal]}

    return out


def strip_engine_owned_fields(
    user_params: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Remove engine-owned keys from ``user_params``; return (cleaned, warnings).

    Used before :func:`merge_params` so an operator cannot override
    ``archetype-policies.assignments`` by hand — that map is always
    rebuilt from the baseline by the scaffold engine.
    """
    cleaned: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for stem, block in user_params.items():
        if not isinstance(block, dict):
            cleaned[stem] = block
            continue
        protected = _ENGINE_OWNED_FIELDS.get(stem, ())
        new_block = dict(block)
        for key in protected:
            if key in new_block:
                del new_block[key]
                warnings.append(
                    f"Ignored operator override for engine-owned field "
                    f"'{stem}.{key}' (Scaffold rebuilds this from the baseline)."
                )
        cleaned[stem] = new_block
    return cleaned, warnings


def merge_params(
    prefilled: dict[str, dict[str, Any]],
    user: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Shallow top-level-keyed merge: operator values override prefilled.

    Merges at the per-template-stem level, then at the per-key level
    within each stem. Nested structures are replaced wholesale — there
    is no deep merge (keeps semantics predictable; the research report's
    Phase D example of ``log-analytics.retentionInDays`` overriding
    without losing ``workspaceName`` works because both are top-level
    keys inside the stem dict).
    """
    merged: dict[str, dict[str, Any]] = {}
    for stem in set(prefilled) | set(user):
        base = dict(prefilled.get(stem, {}))
        overlay = user.get(stem, {}) or {}
        if isinstance(overlay, dict):
            base.update(overlay)
        merged[stem] = base
    return merged


def classify_keys(
    prefilled: dict[str, dict[str, Any]],
    user: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Return ``{stem: {key: 'derived' | 'operator_override'}}`` for trace.

    Useful for ``trace.jsonl`` + ``scaffold.params.auto.json`` so the
    operator can see which values the tool chose vs. which they supplied.
    """
    out: dict[str, dict[str, str]] = {}
    for stem in set(prefilled) | set(user):
        pre = prefilled.get(stem, {}) or {}
        usr = user.get(stem, {}) or {}
        if not isinstance(pre, dict):
            pre = {}
        if not isinstance(usr, dict):
            usr = {}
        row: dict[str, str] = {}
        for k in set(pre) | set(usr):
            if k in usr:
                row[k] = "operator_override"
            else:
                row[k] = "derived"
        if row:
            out[stem] = row
    return out
