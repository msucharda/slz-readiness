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
from ..evaluate.loaders import BASELINE_DIR, RULES_DIR
from ..reconcile import CANONICAL_ROLES
from .template_registry import (
    ALLOWED_TEMPLATES,
    RULE_TO_TEMPLATE,
    TEMPLATE_RUNBOOKS,
    TEMPLATE_SCOPES,
)

# v0.8.0 Track α — whole-word regex over canonical role names as they
# appear in `name: '<role>'` string literals inside templates. The
# template_registry restricts this pattern to the `management-groups`
# template (the only one that hardcodes canonical names today); other
# templates contain the names only in comments or not at all, so the
# substitution is a no-op there. Anchored to the `name:` property to
# avoid collision with displayNames, comments, or any future literals
# that legitimately carry a canonical role word as a value.
_MG_NAME_PROP_RE = re.compile(
    r"(?P<prefix>\bname:\s*')(?P<role>"
    + "|".join(re.escape(r) for r in CANONICAL_ROLES)
    + r")(?P<suffix>')"
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_DIR = REPO_ROOT / "scripts" / "scaffold" / "avm_templates"
RUNBOOKS_DIR = REPO_ROOT / "scripts" / "scaffold" / "runbooks"
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
_MG_FROM_SELECTOR_SCOPE = re.compile(r"^mg/(?P<mg>[A-Za-z0-9_-]+)$")


def _load_rule_scope_overrides() -> dict[str, str]:
    """Map ``rule_id`` → ``mg/<name>`` where the rule's
    ``matcher.selector.scope`` pins a single MG but ``matcher.aggregate`` is
    ``tenant``.

    Evaluate collapses such rules into a single gap with
    ``resource_id="tenant"`` (see ``evaluate/engine.py`` aggregate path),
    which in turn collapses Scaffold's per-scope bucket key. We recover
    the intended per-MG scope from the rule YAML so every MG-scoped
    Confidential rule emits its own Bicep file (one for
    ``confidential_corp``, one for ``confidential_online``).

    Parsed lazily from the rule YAMLs on disk; failures fall through to
    the legacy tenant-scope behaviour (no crash on malformed YAML).
    """
    try:
        import yaml  # local import: yaml is already a dep of evaluate/loaders.
    except ImportError:  # pragma: no cover — yaml is a hard dep of the package.
        return {}
    overrides: dict[str, str] = {}
    if not RULES_DIR.exists():  # pragma: no cover
        return overrides
    for path in RULES_DIR.rglob("*.yml"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 — defensive; any parse failure = skip
            continue
        rule_id = data.get("rule_id")
        matcher = data.get("matcher") or {}
        selector = matcher.get("selector") or {}
        scope = selector.get("scope")
        aggregate = matcher.get("aggregate")
        if not (isinstance(rule_id, str) and isinstance(scope, str)):
            continue
        m = _MG_FROM_SELECTOR_SCOPE.match(scope)
        if not m:
            continue
        if aggregate != "tenant":
            # Non-aggregated rules already carry per-resource scope in the
            # gap's resource_id — nothing to override.
            continue
        overrides[rule_id] = f"scope:mg/{m.group('mg')}"
    return overrides


_RULE_SCOPE_OVERRIDE_CACHE: dict[str, str] | None = None


def _rule_scope_override(rule_id: str) -> str | None:
    global _RULE_SCOPE_OVERRIDE_CACHE
    if _RULE_SCOPE_OVERRIDE_CACHE is None:
        _RULE_SCOPE_OVERRIDE_CACHE = _load_rule_scope_overrides()
    return _RULE_SCOPE_OVERRIDE_CACHE.get(rule_id)


def _scope_hint_for_gap(gap: dict[str, Any]) -> str:
    """Return a filesystem-safe scope hint from a gap's resource_id.

    ``scope:mg/corp`` -> ``corp``; ``tenant`` / empty -> ``""``. Used to
    differentiate per-MG emits.

    When the gap came from an aggregate-tenant rule that nonetheless
    pins a single MG in ``matcher.selector.scope`` (e.g. the two
    Confidential sovereignty rules), we fall back to the rule's pinned
    scope so Scaffold's bucket key remains per-MG. Without this, both
    Confidential rules collapse onto one bucket and only one Bicep file
    is emitted — the exact regression observed in run 20260419T132307Z.
    """
    rid = gap.get("resource_id", "")
    m = _MG_FROM_RESOURCE_ID.match(rid)
    if m:
        return m.group("mg")
    rule_id = gap.get("rule_id")
    if isinstance(rule_id, str):
        override = _rule_scope_override(rule_id)
        if override:
            m2 = _MG_FROM_RESOURCE_ID.match(override)
            if m2:
                return m2.group("mg")
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
    include_placeholders: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Read the archetype JSON + per-assignment JSONs to build the ``assignments`` array.

    Returns ``(assignments, warnings)``. The archetype location is taken from
    ``gap.baseline_ref.path``; the per-assignment JSON lives in the same
    subtree's ``policy_assignments/`` folder.

    When ``rollout_phase == "audit"``, baseline ``Deny`` effect parameter values
    are rewritten to ``Audit`` before emission. Identity type from the baseline
    is propagated via ``identityRequired``.

    ``include_placeholders`` (default False): assignments whose baseline
    parameters still contain ALZ placeholders (all-zero subscription GUIDs,
    literal ``/placeholder/`` segments) are **skipped by default** — emitting
    them makes ``az deployment … create`` what-if fail with opaque validation
    errors. Set True to emit verbatim (legacy behaviour); a LOUD warning is
    always added.
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
    missing_json_skipped: list[str] = []
    pa_dir = BASELINE_DIR / subtree / "policy_assignments"
    for name in names:
        pa_file = pa_dir / f"{name}.alz_policy_assignment.json"
        if not pa_file.exists():
            missing_json_skipped.append(name)
            # Observability hook (v0.12.0): distinguish baseline-integrity
            # skips from placeholder skips so operators can see in
            # trace.jsonl whether the vendored library is stale or whether
            # the ALZ assignment genuinely needs hand-editing.
            _trace.log(
                "scaffold.archetype_skip",
                rule_id=rule_id,
                subtree=subtree,
                assignment_name=name,
                reason="missing_json",
            )
            continue
        pa = json.loads(pa_file.read_text(encoding="utf-8"))
        props = pa.get("properties", {})
        base_params = props.get("parameters", {}) or {}
        # ALZ baseline ships placeholder values (all-zero subscription GUIDs,
        # literal "/placeholder/" segments) for operator-specific resource ids
        # — e.g. private-DNS zone IDs in Deploy-Private-DNS-Zones. Emitting
        # them verbatim makes `az deployment … what-if` fail with opaque
        # validation errors (slz-demo run 20260419T070007Z). Default: skip
        # the assignment entirely and surface the name so the operator can
        # decide whether to fill the values and re-run with
        # --include-placeholders, or accept the governance gap.
        has_placeholder = _contains_placeholder(base_params)
        if has_placeholder:
            placeholder_skipped.append(name)
            if not include_placeholders:
                _trace.log(
                    "scaffold.archetype_skip",
                    rule_id=rule_id,
                    subtree=subtree,
                    assignment_name=name,
                    reason="placeholder",
                )
                continue
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
    if missing_json_skipped:
        # H3 (slz-demo run 20260419T120215Z): aggregate baseline-integrity
        # skips into one bucket so 65+ vendored-file misses are not lost
        # in noise. Distinct from placeholder skips (different remediation).
        sample = ", ".join(missing_json_skipped[:5])
        more = f" (+{len(missing_json_skipped) - 5} more)" if len(missing_json_skipped) > 5 else ""
        warnings.append(
            f"[archetype-policies] BASELINE-INTEGRITY SKIPPED "
            f"{len(missing_json_skipped)} assignment(s) — vendored JSON not "
            f"found under {subtree}/policy_assignments/: {sample}{more}. "
            "Re-vendor the ALZ baseline with `python -m "
            "slz_readiness.evaluate.vendor_baseline --sha <new>`, or repin "
            "the affected rules to the current vendored SHA."
        )
    if rollout_phase == "audit" and audit_rewrite_count:
        warnings.append(
            f"[archetype-policies] rolloutPhase=audit — rewrote {audit_rewrite_count} "
            "baseline Deny effect(s) to Audit. Flip rolloutPhase=enforce in params "
            "after observing compliance data to activate blocking."
        )
    if placeholder_skipped:
        if include_placeholders:
            warnings.append(
                "[archetype-policies] --include-placeholders ON — emitted "
                f"{len(placeholder_skipped)} assignment(s) with unresolved "
                f"placeholder parameter value(s): {', '.join(placeholder_skipped)}. "
                "Operators MUST replace these (all-zero subscription GUIDs, "
                "/placeholder/ segments) in the emitted *.parameters.json before "
                "`az deployment ... create`, or what-if will fail."
            )
        else:
            warnings.append(
                "[archetype-policies] SKIPPED "
                f"{len(placeholder_skipped)} assignment(s) containing unresolved "
                f"baseline placeholders: {', '.join(placeholder_skipped)}. "
                "Governance coverage is REDUCED until these are resolved. "
                "Typical placeholders: all-zero subscription GUIDs in DDoS "
                "protection plan IDs and Private DNS zone resource IDs. To "
                "emit these assignments anyway (and edit the param files "
                "by hand), re-run `slz-scaffold` with --include-placeholders."
            )
    if identity_count:
        warnings.append(
            f"[archetype-policies] {identity_count} assignment(s) require a system-assigned "
            "identity for remediation. After deployment, grant each assignment's "
            "identity.principalId the roleDefinitionIds declared in its policy "
            "definition. See how-to-deploy.md step 'DINE remediation roles'."
        )
    return assignments, warnings


def _rewrite_names_in_bicep(contents: str, alias_map: dict[str, str]) -> tuple[str, int]:
    """Substitute canonical MG role names with tenant names in a Bicep file.

    Only rewrites occurrences inside ``name: '<role>'`` string-literal
    properties (the pattern used by :mod:`management-groups.bicep`).
    Bicep symbolic identifiers (``resource corp ...``) and comments are
    left untouched — symbolic names are internal to the file and the
    compiler doesn't care; comments are documentation.

    Returns the (rewritten_contents, substitution_count).
    """
    if not alias_map:
        return contents, 0
    count = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal count
        role = match.group("role")
        target = alias_map.get(role)
        if not target:
            return match.group(0)
        count += 1
        return f"{match.group('prefix')}{target}{match.group('suffix')}"

    rewritten = _MG_NAME_PROP_RE.sub(_replace, contents)
    return rewritten, count


def _emit(
    out_dir: Path,
    template: str,
    scope_hint: str,
    params: dict[str, Any],
    rule_ids: list[str],
    *,
    alias_map: dict[str, str] | None = None,
    rewrite_names: bool = False,
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
    substitutions = 0
    if rewrite_names and alias_map:
        src_contents = src.read_text(encoding="utf-8")
        rewritten, substitutions = _rewrite_names_in_bicep(src_contents, alias_map)
        dst_bicep.write_text(rewritten, encoding="utf-8")
        if substitutions:
            _trace.log(
                "template.rewrite",
                template=template,
                scope=scope_hint or "tenant",
                substitutions=substitutions,
            )
    else:
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
        deployment_scope=TEMPLATE_SCOPES.get(template, "managementGroup"),
        bicep=dst_bicep.name,
        rule_ids=sorted(set(rule_ids)),
        rollout_phase=params.get("rolloutPhase"),
    )
    # Use forward slashes for cross-platform consistency in manifests.
    # ``scope`` is the per-MG filter (e.g. ``corp``, ``landing_zones``) OR
    # ``"tenant"`` when the template is not per-scope. ``deployment_scope``
    # is the ARM ``targetScope`` the template declares (``managementGroup``,
    # ``subscription``, …) and drives the ``az deployment <verb>`` choice
    # in ``_deploy_commands``. They are DIFFERENT concepts; keeping both
    # avoids the legacy ambiguity that mis-labelled MG-targeted emissions
    # as ``"tenant"``. See slz-demo run 20260419T120215Z (finding M1).
    result: dict[str, Any] = {
        "template": template,
        "scope": scope_hint or "tenant",
        "deployment_scope": TEMPLATE_SCOPES.get(template, "managementGroup"),
        "bicep": dst_bicep.relative_to(out_dir).as_posix(),
        "params": dst_params.relative_to(out_dir).as_posix(),
        "rule_ids": sorted(set(rule_ids)),
        "rollout_phase": params.get("rolloutPhase"),
    }
    if rewrite_names and alias_map:
        result["name_substitutions"] = substitutions

    # Emit runbooks for operators who lack tenant-scope deploy RBAC. These are
    # static artifacts the operator runs manually — the plugin never invokes
    # them (rule 1: read-only Azure). Copied once per template emit; callers
    # dedup if needed.
    runbook_names = TEMPLATE_RUNBOOKS.get(template, [])
    if runbook_names:
        runbooks_out = out_dir / "runbooks"
        runbooks_out.mkdir(exist_ok=True)
        emitted_runbooks: list[str] = []
        for rb_name in runbook_names:
            src_rb = RUNBOOKS_DIR / rb_name
            if not src_rb.exists():
                raise ScaffoldError(f"Runbook file missing: {src_rb}")
            dst_rb = runbooks_out / rb_name
            shutil.copy2(src_rb, dst_rb)
            emitted_runbooks.append(dst_rb.relative_to(out_dir).as_posix())
            _trace.log(
                "runbook.emit",
                template=template,
                runbook=rb_name,
            )
        result["runbooks"] = emitted_runbooks

    return result


def _load_alias_map(run_dir: Path | None) -> dict[str, str]:
    """Return ``{role: customer_mg}`` for non-null entries in ``mg_alias.json``.

    Thin wrapper around :func:`slz_readiness._alias_io.load_alias_map`
    pinned to the ``scaffold`` trace label.
    """
    from .._alias_io import load_alias_map
    return load_alias_map(run_dir, trace_label="scaffold")


def scaffold_for_gaps(
    gaps: list[dict[str, Any]],
    params_by_template: dict[str, dict[str, Any]],
    out_dir: Path,
    *,
    run_dir: Path | None = None,
    rewrite_names: bool | None = None,
    include_placeholders: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Write bicep + params files for `gaps`. Returns (emitted, warnings).

    ``run_dir`` (defaults to ``out_dir``) is the artifacts directory the
    CLI writes into; ``mg_alias.json``, when present, is loaded from here
    so Scaffold can advertise brownfield retargeting in
    ``how-to-deploy.md``. The skip-existing transform is implicit: the
    Track-2 matcher already excludes def-id-matched assignments from
    ``gap.observed.missing``, which ``_resolve_archetype_assignments``
    consumes verbatim.

    ``rewrite_names`` (v0.8.0, tri-state in v0.12.0): when True AND
    ``mg_alias.json`` has non-null entries, rewrite canonical MG role
    names to the tenant's names inside emitted ``.bicep`` files (see
    :func:`_rewrite_names_in_bicep`).

    * ``True`` — always rewrite (explicit operator opt-in).
    * ``False`` — never rewrite (explicit operator opt-out).
    * ``None`` (default) — auto-enable iff ``mg_alias.json`` is present
      AND at least one ``management-groups`` createX flag is false
      (i.e. a MG is already on the tenant under its aliased name).
      This is the brownfield default — without it the MG Bicep emits
      canonical names, fails to match the aliased MGs on the tenant,
      and produces a disconnected parallel tree at the tenant root
      (root cause of blocker #2 in run 20260419T132307Z).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bicep").mkdir(exist_ok=True)
    (out_dir / "params").mkdir(exist_ok=True)

    # v0.7.0: brownfield retargeting hint. The actual scope-rewrite happens
    # at Evaluate-time (selector aliasing) and at deploy-time (operator picks
    # MG_ID per ``how-to-deploy.md``). This loader exists so Scaffold can
    # surface the alias mapping in warnings + how-to-deploy.md.
    alias_map = _load_alias_map(run_dir if run_dir is not None else out_dir)

    # Auto-flip when rewrite_names was not explicitly set: enable when
    # alias entries are present AND the params for management-groups
    # carry any createX=false (i.e. we're in brownfield with at least
    # one already-present MG the Bicep would otherwise try to recreate).
    mg_params = params_by_template.get("management-groups") or {}
    has_brownfield_create_flag = any(
        k.startswith("create") and v is False for k, v in mg_params.items()
    )
    auto_flipped = False
    if rewrite_names is None:
        if alias_map and has_brownfield_create_flag:
            rewrite_names = True
            auto_flipped = True
        else:
            rewrite_names = False

    warnings: list[str] = []
    if alias_map:
        warnings.append(
            "[brownfield] mg_alias.json detected — "
            f"{len(alias_map)} canonical role(s) retargeted to customer MGs: "
            + ", ".join(f"{k}→{v}" for k, v in sorted(alias_map.items()))
            + ". Use these names when filling MG_ID placeholders in how-to-deploy.md."
        )
    if auto_flipped:
        warnings.append(
            "[brownfield] rewrite_names auto-enabled because mg_alias.json is "
            "present AND at least one management-groups createX flag is false. "
            "Canonical SLZ names in management-groups.bicep will be rewritten "
            "to tenant names so existing (aliased) MGs are not re-created. "
            "Pass --no-rewrite-names to override."
        )
    if rewrite_names and alias_map:
        warnings.append(
            f"[brownfield] --rewrite-names ON — emitted Bicep will use tenant MG "
            f"names (apply-ready for this tenant). "
            f"{len(alias_map)} canonical role(s) will be substituted in "
            "management-groups.bicep; other templates are name-agnostic."
        )
    elif rewrite_names and not alias_map:
        warnings.append(
            "[brownfield] --rewrite-names supplied but mg_alias.json is empty or "
            "missing; emitting canonical SLZ names (greenfield behaviour)."
        )
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
                bucket["gap"],
                rollout_phase=rollout_phase or "audit",
                include_placeholders=include_placeholders,
            )
            warnings.extend(
                f"[{tmpl}:{scope or 'tenant'}] {msg}" if not msg.startswith("[") else msg
                for msg in w
            )
            # v0.7.0: surface skip-existing — when the matcher's def-id
            # fallback collapsed required→missing, scaffold emits fewer
            # assignments. Tell the operator which were considered already
            # present (by name OR equivalent definition id).
            obs = bucket["gap"].get("observed") or {}
            present_names = obs.get("present") or []
            matched_by_defid = obs.get("matched_by_defid") or []
            skipped_total = len(present_names) + len(matched_by_defid)
            if skipped_total:
                _trace.log(
                    "scaffold.skip_existing",
                    template=tmpl,
                    scope=scope or "tenant",
                    skipped_count=skipped_total,
                    skipped_names=sorted(present_names),
                    matched_by_defid=matched_by_defid,
                )
                if matched_by_defid:
                    pairs = ", ".join(
                        f"{m.get('required_name','?')}→{m.get('observed_name','?')}"
                        for m in matched_by_defid
                    )
                    warnings.append(
                        f"[{tmpl}:{scope or 'tenant'}] skipped {skipped_total} assignment(s) "
                        f"already on tenant ({len(present_names)} by name, "
                        f"{len(matched_by_defid)} by policyDefinitionId equivalence). "
                        f"Equivalence pairs: {pairs}."
                    )
                else:
                    warnings.append(
                        f"[{tmpl}:{scope or 'tenant'}] skipped {skipped_total} assignment(s) "
                        "already present on tenant."
                    )
            if not assignments:
                _trace.log(
                    "scaffold.archetype_skip",
                    rule_id=bucket["gap"].get("rule_id"),
                    scope=scope or "tenant",
                    reason="no_assignments_resolved",
                )
                warnings.append(
                    f"[{tmpl}:{scope or 'tenant'}] No resolvable assignments; skipping emit"
                )
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
            if not params.get("listOfAllowedLocations"):
                warnings.append(
                    "[sovereignty-confidential-policies] listOfAllowedLocations is empty — "
                    "the Confidential policy set will flag every non-global region as "
                    "non-compliant under audit and Deny writes to every non-global region "
                    "under enforce. Populate the params file with your sovereign regions "
                    "before deploying."
                )
                params["listOfAllowedLocations"] = []
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
            emitted.append(
                _emit(
                    out_dir,
                    tmpl,
                    scope,
                    params,
                    bucket["rule_ids"],
                    alias_map=alias_map,
                    rewrite_names=rewrite_names,
                )
            )
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
