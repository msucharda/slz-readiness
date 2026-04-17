"""Scaffold engine.

Consumes gaps.json and emits Bicep + params for each gap by copying a pinned
template from scripts/scaffold/avm_templates/ and validating the caller's
parameters against the matching JSON schema. Never produces free-form Bicep.

v0.2.0 changes:
* Dedup by ``(template, scope)`` rather than template alone, so per-MG archetypes
  (landing_zones, corp, platform, …) each get their own Bicep file instead of
  being collapsed into one.
* Auto-populate params for the ``archetype-policies`` template from the vendored
  archetype definition + referenced policy_assignment JSONs.
* Emit ``warnings`` in ``scaffold.manifest.json`` (e.g. when the sovereignty
  Global assignment has no listOfAllowedLocations).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .. import _trace
from ..evaluate.loaders import BASELINE_DIR
from .template_registry import ALLOWED_TEMPLATES, RULE_TO_TEMPLATE

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_DIR = REPO_ROOT / "scripts" / "scaffold" / "avm_templates"
SCHEMAS_DIR = REPO_ROOT / "scripts" / "scaffold" / "param_schemas"

# Which subtree a policy_assignment file lives under, keyed by the archetype's
# baseline path. Deterministic — mirrors the vendored layout.
_SUBTREE_FOR_ARCHETYPE_RULE: dict[str, str] = {
    "archetype.alz_connectivity_policies_applied": "platform/alz",
    "archetype.alz_corp_policies_applied": "platform/alz",
    "archetype.alz_decommissioned_policies_applied": "platform/alz",
    "archetype.alz_identity_policies_applied": "platform/alz",
    "archetype.alz_landing_zones_policies_applied": "platform/alz",
    "archetype.alz_platform_policies_applied": "platform/alz",
    "archetype.alz_sandbox_policies_applied": "platform/alz",
    "archetype.slz_public_policies_applied": "platform/slz",
}

# Templates for which we emit one file per scope (gap.resource_id).
_PER_SCOPE_TEMPLATES = {
    "archetype-policies",
    "sovereignty-confidential-policies",
}


class ScaffoldError(RuntimeError):
    pass


def _load_schema(template_stem: str) -> dict[str, Any]:
    schema_path = SCHEMAS_DIR / f"{template_stem}.schema.json"
    if not schema_path.exists():
        raise ScaffoldError(f"Missing param schema for template '{template_stem}'")
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _validate_params(template_stem: str, params: dict[str, Any]) -> None:
    schema = _load_schema(template_stem)
    errors = sorted(Draft202012Validator(schema).iter_errors(params), key=lambda e: tuple(str(p) for p in e.path))
    if errors:
        msgs = "; ".join(f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors)
        raise ScaffoldError(f"Invalid params for {template_stem}: {msgs}")


_MG_FROM_RESOURCE_ID = re.compile(r"^scope:mg/(?P<mg>[A-Za-z0-9_-]+)$")


def _scope_hint_for_gap(gap: dict[str, Any]) -> str:
    """Return a filesystem-safe scope hint from a gap's resource_id.

    ``scope:mg/corp`` -> ``corp``; ``tenant`` / empty -> ``""``. Used to
    differentiate per-MG emits.
    """
    rid = gap.get("resource_id", "")
    m = _MG_FROM_RESOURCE_ID.match(rid)
    if m:
        return m.group("mg")
    return ""


def _resolve_archetype_assignments(gap: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Read the archetype JSON + per-assignment JSONs to build the ``assignments`` array.

    Returns ``(assignments, warnings)``. The archetype location is taken from
    ``gap.baseline_ref.path``; the per-assignment JSON lives in the same
    subtree's ``policy_assignments/`` folder.
    """
    warnings: list[str] = []
    baseline_ref = gap.get("baseline_ref") or {}
    arch_path = baseline_ref.get("path", "")
    rule_id = gap.get("rule_id", "")
    subtree = _SUBTREE_FOR_ARCHETYPE_RULE.get(rule_id)
    if not subtree:
        raise ScaffoldError(f"No subtree mapping for archetype rule '{rule_id}' — update _SUBTREE_FOR_ARCHETYPE_RULE")
    arch_file = BASELINE_DIR / arch_path
    if not arch_file.exists():
        raise ScaffoldError(f"Archetype file missing: {arch_file}")
    archetype = json.loads(arch_file.read_text(encoding="utf-8"))

    # Prefer the gap's observed "missing" list (so we only scaffold what's not
    # already present). Fall back to the full required list if the gap shape is
    # unexpected (e.g. synthetic test fixtures).
    observed = gap.get("observed") or {}
    names = observed.get("missing")
    if not names:
        names = archetype.get("policy_assignments", [])

    assignments: list[dict[str, Any]] = []
    pa_dir = BASELINE_DIR / subtree / "policy_assignments"
    for name in names:
        pa_file = pa_dir / f"{name}.alz_policy_assignment.json"
        if not pa_file.exists():
            warnings.append(f"Policy assignment JSON missing for '{name}' ({subtree}); skipped")
            continue
        pa = json.loads(pa_file.read_text(encoding="utf-8"))
        props = pa.get("properties", {})
        assignments.append(
            {
                "name": pa.get("name", name),
                "displayName": props.get("displayName", name),
                "policyDefinitionId": props.get("policyDefinitionId", ""),
                "enforcementMode": props.get("enforcementMode", "Default"),
                "parameters": props.get("parameters", {}) or {},
            }
        )
    return assignments, warnings


def _emit(
    out_dir: Path,
    template: str,
    scope_hint: str,
    params: dict[str, Any],
    rule_ids: list[str],
) -> dict[str, Any]:
    if template not in ALLOWED_TEMPLATES:
        raise ScaffoldError(f"Template '{template}' not in ALLOWED_TEMPLATES")
    src = TEMPLATES_DIR / f"{template}.bicep"
    if not src.exists():
        raise ScaffoldError(f"Template file missing: {src}")
    _validate_params(template, params)

    suffix = f"-{scope_hint}" if scope_hint else ""
    filename = f"{template}{suffix}"
    dst_bicep = out_dir / "bicep" / f"{filename}.bicep"
    dst_params = out_dir / "params" / f"{filename}.parameters.json"
    shutil.copy2(src, dst_bicep)
    dst_params.write_text(
        json.dumps(
            {
                "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
                "contentVersion": "1.0.0.0",
                "parameters": {k: {"value": v} for k, v in params.items()},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _trace.log(
        "template.emit",
        template=template,
        scope=scope_hint or "tenant",
        bicep=dst_bicep.name,
        rule_ids=sorted(set(rule_ids)),
    )
    # Use forward slashes for cross-platform consistency in manifests.
    return {
        "template": template,
        "scope": scope_hint or "tenant",
        "bicep": dst_bicep.relative_to(out_dir).as_posix(),
        "params": dst_params.relative_to(out_dir).as_posix(),
        "rule_ids": sorted(set(rule_ids)),
    }


def scaffold_for_gaps(
    gaps: list[dict[str, Any]],
    params_by_template: dict[str, dict[str, Any]],
    out_dir: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Write bicep + params files for `gaps`. Returns (emitted, warnings)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bicep").mkdir(exist_ok=True)
    (out_dir / "params").mkdir(exist_ok=True)

    warnings: list[str] = []
    # Group: key = (template, scope_hint) -> list of contributing rule_ids + one representative gap
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for gap in gaps:
        rule_id = gap.get("rule_id")
        if gap.get("status") == "unknown":
            # Can't scaffold a fix we couldn't verify.
            continue
        tmpl = RULE_TO_TEMPLATE.get(rule_id or "")
        if tmpl is None:
            continue
        scope = _scope_hint_for_gap(gap) if tmpl in _PER_SCOPE_TEMPLATES else ""
        key = (tmpl, scope)
        bucket = buckets.setdefault(key, {"rule_ids": [], "gap": gap})
        bucket["rule_ids"].append(rule_id)

    emitted: list[dict[str, Any]] = []
    for (tmpl, scope), bucket in sorted(buckets.items()):
        user_params = params_by_template.get(tmpl, {})
        if tmpl == "archetype-policies":
            assignments, w = _resolve_archetype_assignments(bucket["gap"])
            warnings.extend(f"[{tmpl}:{scope}] {msg}" for msg in w)
            if not assignments:
                warnings.append(f"[{tmpl}:{scope}] No resolvable assignments; skipping emit")
                continue
            params = {"assignments": assignments}
            if "defaultEnforcementMode" in user_params:
                params["defaultEnforcementMode"] = user_params["defaultEnforcementMode"]
        elif tmpl == "sovereignty-global-policies":
            params = dict(user_params)
            if not params.get("listOfAllowedLocations"):
                warnings.append(
                    "[sovereignty-global-policies] listOfAllowedLocations is empty — "
                    "the Global policy set will deny all locations once assigned. "
                    "Populate the params file with your sovereign regions before deploying."
                )
                params["listOfAllowedLocations"] = []
        else:
            params = user_params
        emitted.append(_emit(out_dir, tmpl, scope, params, bucket["rule_ids"]))

    return emitted, warnings
