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

v0.3.0 changes (phased rollout + identity):
* ``rolloutPhase`` param threaded through the three Deny-class templates.
  Default ``audit`` — operators must opt into ``enforce``. When ``audit``, the
  engine rewrites baseline ``effect: Deny`` values to ``Audit`` at emit time
  (whitelist-based: only keys named ``effect`` or ending in ``Effect``/``effect``).
* ``identityRequired`` propagated from each baseline assignment's ``identity.type``
  so DINE/Modify/Append remediation policies keep their system-assigned identity
  instead of silently losing it.
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

# Templates that carry Deny-class effects and accept a rolloutPhase param.
_PHASED_TEMPLATES = {
    "archetype-policies",
    "sovereignty-global-policies",
    "sovereignty-confidential-policies",
}

# Policy-effect values we rewrite when rolloutPhase=audit. Any value in this
# set is downshifted to "Audit". Everything else (DINE/Append/Modify/DenyAction/
# AuditIfNotExists/Disabled/Manual/already-Audit) is left alone.
_DENY_EFFECT_VALUES = {"Deny", "deny", "DENY"}

# Placeholder patterns we detect in baseline policy-assignment parameters.
# The ALZ library ships policy assignments that reference external resources
# (e.g. a DDoS plan resource id) using placeholder strings the operator is
# expected to replace before deployment. Emitting these verbatim produces
# what-if errors because the referenced resource does not exist. We detect,
# warn, and skip.
_PLACEHOLDER_RE = re.compile(
    # Only match the well-known ALZ placeholder shapes that produce what-if
    # errors: the all-zeroes subscription GUID, or a literal "/placeholder/"
    # path segment. A loose \bplaceholder\b match would over-fire on policy
    # defaultValue / metadata strings that mention the word innocuously.
    r"/subscriptions/0{8}-0{4}-0{4}-0{4}-0{12}/"
    r"|/placeholder(/|$)",
    re.IGNORECASE,
)


def _contains_placeholder(value: Any) -> bool:
    """Recursively check ``value`` for any placeholder string match."""
    if isinstance(value, str):
        return bool(_PLACEHOLDER_RE.search(value))
    if isinstance(value, dict):
        return any(_contains_placeholder(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_placeholder(v) for v in value)
    return False

# Parameter-name predicate for the audit rewrite. Key name must contain
# "effect" (case-insensitive) — whitelist-based to avoid rewriting an
# unrelated parameter that happens to carry the string "Deny" as a value.
# Combined with the value-match ("Deny") this is safe: both name and value
# must indicate an effect.
_EFFECT_PARAM_RE = re.compile(r"[Ee]ffect")


class ScaffoldError(RuntimeError):
    pass


def _load_schema(template_stem: str) -> dict[str, Any]:
    schema_path = SCHEMAS_DIR / f"{template_stem}.schema.json"
    if not schema_path.exists():
        raise ScaffoldError(f"Missing param schema for template '{template_stem}'")
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _validate_params(template_stem: str, params: dict[str, Any]) -> None:
    schema = _load_schema(template_stem)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(params),
        key=lambda e: tuple(str(p) for p in e.path),
    )
    if errors:
        msgs = "; ".join(
            f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors
        )
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


def _downshift_deny_to_audit(parameters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Return a copy of ``parameters`` with any Deny-valued effect key rewritten to Audit.

    The rewrite is whitelist-scoped to keys whose name is exactly ``effect`` or
    ends in ``Effect`` / ``effect`` (e.g. ``effectNotAllowedResources``,
    ``effectDenyVnetPeering``). Everything else — including keys whose *value*
    happens to contain the string ``Deny`` — is preserved verbatim.

    Returns ``(new_parameters, rewritten_count)``.
    """
    if not parameters:
        return parameters, 0
    out: dict[str, Any] = {}
    rewritten = 0
    for key, val in parameters.items():
        if _EFFECT_PARAM_RE.search(key) and isinstance(val, dict):
            inner = val.get("value")
            if isinstance(inner, str) and inner in _DENY_EFFECT_VALUES:
                out[key] = {**val, "value": "Audit"}
                rewritten += 1
                continue
        out[key] = val
    return out, rewritten


def _resolve_archetype_assignments(
    gap: dict[str, Any],
    *,
    rollout_phase: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Read the archetype JSON + per-assignment JSONs to build the ``assignments`` array.

    Returns ``(assignments, warnings)``. The archetype location is taken from
    ``gap.baseline_ref.path``; the per-assignment JSON lives in the same
    subtree's ``policy_assignments/`` folder.

    When ``rollout_phase == "audit"``, baseline ``Deny`` effect parameter values
    are rewritten to ``Audit`` before emission. Identity type from the baseline
    is propagated via ``identityRequired``.
    """
    warnings: list[str] = []
    baseline_ref = gap.get("baseline_ref") or {}
    arch_path = baseline_ref.get("path", "")
    rule_id = gap.get("rule_id", "")
    subtree = _SUBTREE_FOR_ARCHETYPE_RULE.get(rule_id)
    if not subtree:
        raise ScaffoldError(
            f"No subtree mapping for archetype rule '{rule_id}' — "
            "update _SUBTREE_FOR_ARCHETYPE_RULE"
        )
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
    identity_count = 0
    audit_rewrite_count = 0
    placeholder_skipped: list[str] = []
    pa_dir = BASELINE_DIR / subtree / "policy_assignments"
    for name in names:
        pa_file = pa_dir / f"{name}.alz_policy_assignment.json"
        if not pa_file.exists():
            warnings.append(f"Policy assignment JSON missing for '{name}' ({subtree}); skipped")
            continue
        pa = json.loads(pa_file.read_text(encoding="utf-8"))
        props = pa.get("properties", {})
        base_params = props.get("parameters", {}) or {}
        # ALZ baseline ships placeholder values (all-zero subscription GUIDs,
        # literal "/placeholder/" segments) for operator-specific resource ids
        # — e.g. private-DNS zone IDs in Deploy-Private-DNS-Zones. We keep the
        # assignment (losing it drops governance) but surface the name so the
        # operator can fill the values before deploy; skipping silently is the
        # bug that triggered the "what-if blew up" from session aa4e7b2f.
        if _contains_placeholder(base_params):
            placeholder_skipped.append(name)
        if rollout_phase == "audit":
            base_params, n = _downshift_deny_to_audit(base_params)
            audit_rewrite_count += n
        identity_type = (pa.get("identity") or {}).get("type", "None")
        identity_required = identity_type != "None"
        if identity_required:
            identity_count += 1
        assignments.append(
            {
                "name": pa.get("name", name),
                "displayName": props.get("displayName", name),
                "policyDefinitionId": props.get("policyDefinitionId", ""),
                "enforcementMode": props.get("enforcementMode", "Default"),
                "parameters": base_params,
                "identityRequired": identity_required,
            }
        )
    if rollout_phase == "audit" and audit_rewrite_count:
        warnings.append(
            f"[archetype-policies] rolloutPhase=audit — rewrote {audit_rewrite_count} "
            "baseline Deny effect(s) to Audit. Flip rolloutPhase=enforce in params "
            "after observing compliance data to activate blocking."
        )
    if placeholder_skipped:
        warnings.append(
            "[archetype-policies] "
            f"{len(placeholder_skipped)} assignment(s) contain placeholder "
            f"parameter value(s) that operators MUST replace before deploy "
            f"(what-if will fail otherwise): {', '.join(placeholder_skipped)}. "
            "Typical placeholders: all-zero subscription GUIDs in DDoS protection "
            "plan IDs and Private DNS zone resource IDs. Edit the emitted "
            "*.bicepparam / *.parameters.json before running `az deployment ... create`."
        )
    if identity_count:
        warnings.append(
            f"[archetype-policies] {identity_count} assignment(s) require a system-assigned "
            "identity for remediation. After deployment, grant each assignment's "
            "identity.principalId the roleDefinitionIds declared in its policy "
            "definition. See how-to-deploy.md step 'DINE remediation roles'."
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
        rollout_phase=params.get("rolloutPhase"),
    )
    # Use forward slashes for cross-platform consistency in manifests.
    return {
        "template": template,
        "scope": scope_hint or "tenant",
        "bicep": dst_bicep.relative_to(out_dir).as_posix(),
        "params": dst_params.relative_to(out_dir).as_posix(),
        "rule_ids": sorted(set(rule_ids)),
        "rollout_phase": params.get("rolloutPhase"),
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
        # Default rolloutPhase=audit for all Deny-class templates unless the
        # operator explicitly opts into enforce.
        rollout_phase = (
            user_params.get("rolloutPhase", "audit") if tmpl in _PHASED_TEMPLATES else None
        )
        if tmpl == "archetype-policies":
            assignments, w = _resolve_archetype_assignments(
                bucket["gap"], rollout_phase=rollout_phase or "audit"
            )
            warnings.extend(
                f"[{tmpl}:{scope}] {msg}" if not msg.startswith("[") else msg for msg in w
            )
            if not assignments:
                warnings.append(f"[{tmpl}:{scope}] No resolvable assignments; skipping emit")
                continue
            params: dict[str, Any] = {"assignments": assignments}
            if "defaultEnforcementMode" in user_params:
                params["defaultEnforcementMode"] = user_params["defaultEnforcementMode"]
            if "identityLocation" in user_params:
                params["identityLocation"] = user_params["identityLocation"]
            params["rolloutPhase"] = rollout_phase or "audit"
        elif tmpl == "sovereignty-global-policies":
            params = dict(user_params)
            params.setdefault("rolloutPhase", "audit")
            if not params.get("listOfAllowedLocations"):
                warnings.append(
                    "[sovereignty-global-policies] listOfAllowedLocations is empty — "
                    "the Global policy set will flag every location as non-compliant. "
                    "Populate the params file with your sovereign regions before deploying."
                )
                params["listOfAllowedLocations"] = []
        elif tmpl == "sovereignty-confidential-policies":
            params = dict(user_params)
            params.setdefault("rolloutPhase", "audit")
        else:
            params = dict(user_params)
        # Phase-level advisory warnings (one per emitted template).
        if tmpl in _PHASED_TEMPLATES:
            phase = params.get("rolloutPhase", "audit")
            if phase == "enforce":
                warnings.append(
                    f"[{tmpl}:{scope or 'tenant'}] rolloutPhase=enforce — assignments will "
                    "Deny non-compliant writes on first deploy. Confirm an Audit wave "
                    "already ran and compliance data was reviewed."
                )
            else:
                warnings.append(
                    f"[{tmpl}:{scope or 'tenant'}] rolloutPhase=audit — assignments will "
                    "log non-compliance without blocking. Re-run scaffold with "
                    "rolloutPhase=enforce after the observe window to activate Deny."
                )
        try:
            emitted.append(_emit(out_dir, tmpl, scope, params, bucket["rule_ids"]))
        except ScaffoldError as exc:
            # Fix 6: demote per-bucket emit failures to warnings so one bad
            # gap doesn't blow up the entire scaffold run. The caller (cli)
            # still fails if zero templates emit.
            warnings.append(
                f"[{tmpl}:{scope or 'tenant'}] SKIPPED — {exc}"
            )
            _trace.log(
                "scaffold.emit_skipped",
                template=tmpl,
                scope=scope,
                error=str(exc),
            )
            continue

    return emitted, warnings
