"""Emit a one-shot ``deploy-all.{ps1,sh}`` orchestrator alongside Bicep.

v0.14.0 opt-in feature. When ``slz-scaffold --emit-deploy-script`` is passed,
this module writes two pairs of shell scripts into ``artifacts/<run>/runbooks/``:

* ``deploy-all.{ps1,sh}`` — iterates the emitted templates in the canonical
  ``_DEPLOY_ORDER``, runs ``az deployment ... what-if`` for every template
  first, then (if ``-Apply`` / ``--apply`` is passed) runs ``create``. Wave 1
  only — Wave 2 (``rolloutPhase=enforce``) stays a deliberate separate action
  gated by the observe window documented in ``how-to-deploy.md``.
* ``grant-dine-roles.{ps1,sh}`` — emitted whenever any ``archetype-policies``
  assignment is present. Reads the ``principalId`` of each deployed policy
  assignment's system-assigned identity and grants the declared
  ``roleDefinitionIds`` at MG scope. A strict *post-Wave-1* script (the
  identities don't exist until after ``create`` succeeds).

Contract preserved:

* The scaffold phase never runs deployments. These scripts are *emitted*;
  the human operator runs them.
* ``hooks/pre_tool_use.py`` still blocks the agent from executing either
  emitted script — the DENY regex catches ``deploy`` / ``create`` /
  ``assign`` anywhere in the command string.
* Brownfield gate: non-empty ``mg_alias.json`` → the script surfaces the
  alias table and refuses to run unless ``-SkipMgPrereq`` /
  ``--skip-mg-prereq`` is passed. Bicep cannot re-parent an existing MG,
  so operators MUST verify parent topology before deploy.

See ``.github/instructions/slz-readiness.instructions.md:13,38`` and
``AGENTS.md`` §1/§6/§7 for the HITL contract this module operates under.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import _trace
from .template_registry import TEMPLATE_SCOPES


@dataclass(frozen=True)
class _Vars:
    """Resolved defaults for the runbook's top-level variables.

    Each ``*_is_placeholder`` flag is ``True`` when the renderer had to fall
    back to a ``<...>`` sentinel because nothing in ``params_by_template`` /
    ``alias_map`` / ``tenant_id`` could supply a concrete value. The
    placeholder-check guard in the emitted scripts only references vars
    whose flag is ``True`` — a fully-derived run emits zero angle-bracket
    placeholders and zero guard lines.
    """

    location: str
    location_is_placeholder: bool
    tenant_root_mg: str
    tenant_root_is_placeholder: bool
    slz_root_mg: str
    slz_root_is_placeholder: bool


def _resolve_vars(
    params_by_template: dict[str, dict[str, Any]] | None,
    alias_map: dict[str, str] | None,
    tenant_id: str | None,
) -> _Vars:
    """Derive runbook-level defaults from scaffold params + alias + tenant.

    Precedence (highest → lowest):

    * ``$location`` — ``sovereignty-global-policies.listOfAllowedLocations[0]``
      (the modal region ``prefill_params`` writes), else
      ``log-analytics.location``, else ``<your-region>`` placeholder.
    * ``$tenantRootMgId`` — ``management-groups.parentManagementGroupId``
      (prefill writes the observed parent of the SLZ root; falls back to
      ``tenant_id`` for greenfield), else the supplied ``tenant_id``, else
      ``<your-tenant-root-mg-id>`` placeholder.
    * ``$slzRootMgId`` — ``alias_map["slz"]`` (same behaviour as before),
      else ``<your-slz-root-mg-id>`` placeholder.

    Pure function. No I/O. Safe to call multiple times.
    """
    params_by_template = params_by_template or {}
    alias_map = alias_map or {}

    # $location
    location: str | None = None
    sgp = params_by_template.get("sovereignty-global-policies") or {}
    if isinstance(sgp, dict):
        loc_list = sgp.get("listOfAllowedLocations")
        if isinstance(loc_list, list) and loc_list:
            first = loc_list[0]
            if isinstance(first, str) and first:
                location = first
    if location is None:
        la = params_by_template.get("log-analytics") or {}
        if isinstance(la, dict):
            la_loc = la.get("location")
            if isinstance(la_loc, str) and la_loc:
                location = la_loc

    # $tenantRootMgId
    tenant_root: str | None = None
    mg_block = params_by_template.get("management-groups") or {}
    if isinstance(mg_block, dict):
        parent = mg_block.get("parentManagementGroupId")
        if isinstance(parent, str) and parent:
            tenant_root = parent
    if tenant_root is None and isinstance(tenant_id, str) and tenant_id:
        tenant_root = tenant_id

    # $slzRootMgId
    slz_alias = alias_map.get("slz")
    slz_root = slz_alias if isinstance(slz_alias, str) and slz_alias else None

    return _Vars(
        location=location or "<your-region>",
        location_is_placeholder=location is None,
        tenant_root_mg=tenant_root or "<your-tenant-root-mg-id>",
        tenant_root_is_placeholder=tenant_root is None,
        slz_root_mg=slz_root or "<your-slz-root-mg-id>",
        slz_root_is_placeholder=slz_root is None,
    )

# Canonical deploy order — re-declared here to avoid a circular import from
# cli.py. Must stay in lockstep with cli._DEPLOY_ORDER; a golden test pins
# equivalence (``test_scaffold_deploy_script.test_order_matches_cli``).
_DEPLOY_ORDER: list[str] = [
    "management-groups",
    "log-analytics",
    "alz-policy-definitions",
    "sovereignty-global-policies",
    "archetype-policies",
    "sovereignty-confidential-policies",
    "policy-assignment",
    "role-assignment",
]

# Templates deployed in Stage 1 (before token refresh). These either create
# the MG hierarchy itself or are subscription-scoped (log-analytics) and don't
# need the refreshed MG claims in the token. Everything else goes to Stage 2.
_STAGE1_TEMPLATES: set[str] = {"management-groups", "log-analytics"}


class _Step:
    """One ``what-if``/``create`` pair for a single emitted template.

    Encapsulates the per-template quirks resolved in ``cli._deploy_commands``
    (scope, alias-resolved MG variable, sovereign-root hint) so both shell
    renderers share a single decision point.
    """

    __slots__ = (
        "template",
        "bicep",
        "params",
        "scope",
        "phase",
        "scope_name",
        "mg_bash_var",
        "mg_pwsh_var",
        "mg_note",
    )

    def __init__(
        self,
        *,
        template: str,
        bicep: str,
        params: str,
        scope: str,
        phase: str | None,
        scope_name: str,
        mg_bash_var: str | None,
        mg_pwsh_var: str | None,
        mg_note: str | None,
    ) -> None:
        self.template = template
        self.bicep = bicep
        self.params = params
        self.scope = scope
        self.phase = phase
        self.scope_name = scope_name
        self.mg_bash_var = mg_bash_var
        self.mg_pwsh_var = mg_pwsh_var
        self.mg_note = mg_note


def _plan_steps(
    emitted: list[dict[str, Any]],
    *,
    alias_map: dict[str, str] | None = None,
) -> list[_Step]:
    """Derive the structured deploy plan from the emit manifest.

    Mirrors the per-template resolution logic in ``cli._deploy_commands``
    (sovereignty-global → ``$SLZ_ROOT_MG_ID``, alz-policy-definitions → same,
    sovereignty-confidential per-scope alias lookup). Intentional small
    duplication of cli._deploy_commands so both doc emission and script
    emission stay robust to independent evolution — the golden test in
    ``tests/test_scaffold_deploy_script.py`` pins equivalence.
    """
    alias_map = alias_map or {}
    by_order = sorted(
        emitted,
        key=lambda e: (
            _DEPLOY_ORDER.index(e["template"]) if e["template"] in _DEPLOY_ORDER else 99,
            e.get("scope", ""),
        ),
    )
    steps: list[_Step] = []
    slz_alias = alias_map.get("slz")
    for e in by_order:
        template = e.get("template", "")
        scope = TEMPLATE_SCOPES.get(template, "managementGroup")
        scope_name = e.get("scope") or ""
        mg_bash_var: str | None = None
        mg_pwsh_var: str | None = None
        mg_note: str | None = None
        if template == "sovereignty-global-policies":
            mg_bash_var = "$SLZ_ROOT_MG_ID"
            mg_pwsh_var = "$slzRootMgId"
            if not slz_alias:
                mg_note = (
                    "no mg_alias.json entry for `slz`; SLZ_ROOT_MG_ID "
                    "defaults to placeholder — populate it below."
                )
        elif template == "alz-policy-definitions":
            mg_bash_var = "$SLZ_ROOT_MG_ID"
            mg_pwsh_var = "$slzRootMgId"
            mg_note = (
                "Custom ALZ policyDefinitions — deploy at SLZ intermediate-root MG."
            )
        elif template == "sovereignty-confidential-policies" and scope_name:
            resolved = alias_map.get(scope_name) or scope_name
            mg_bash_var = f'"{resolved}"'
            mg_pwsh_var = f'"{resolved}"'
            mg_note = (
                f"target MG for confidential `{scope_name}`: {resolved} "
                "(from mg_alias.json or canonical scope name)"
            )
        elif template == "archetype-policies":
            # Archetype policies are per-archetype MG assignments. Without
            # per-scope resolution every archetype collapses onto a single
            # ``$MG_ID`` placeholder and the operator has no way to tell
            # which MG each assignment actually targets. Resolve from
            # mg_alias.json when available, fall back to the canonical
            # archetype name (corp / online / identity / ...).
            resolved = alias_map.get(scope_name) or scope_name or "<archetype-mg>"
            mg_bash_var = f'"{resolved}"'
            mg_pwsh_var = f'"{resolved}"'
            mg_note = (
                f"target MG for archetype `{scope_name}`: {resolved} "
                "(from mg_alias.json or canonical archetype name)"
            )
        elif template == "management-groups":
            # The MG-hierarchy template creates child MGs under a parent.
            # That parent is the tenant-root MG (which equals the tenant
            # id for most tenants), NOT an archetype MG. See the header
            # comment of scripts/scaffold/avm_templates/management-groups.bicep.
            mg_bash_var = "$TENANT_ROOT_MG_ID"
            mg_pwsh_var = "$tenantRootMgId"
            mg_note = (
                "hierarchy deploy: scope is the tenant-root MG (parent of slz). "
                "For most tenants this equals the tenant id."
            )
        elif template in {"policy-assignment", "role-assignment"} and scope_name:
            # Scope field carries the concrete target MG id for these
            # templates (set by the scaffold engine from the matched gap's
            # archetype). Inline it directly so operators don't have to
            # rebind a generic ``$MG_ID`` for every assignment deploy — the
            # value was derived from findings + alias_map at scaffold time.
            resolved = alias_map.get(scope_name) or scope_name
            mg_bash_var = f'"{resolved}"'
            mg_pwsh_var = f'"{resolved}"'
            mg_note = f"target MG for {template}: {resolved}"
        steps.append(
            _Step(
                template=template,
                bicep=e.get("bicep", ""),
                params=e.get("params", ""),
                scope=scope,
                phase=e.get("rollout_phase"),
                scope_name=scope_name,
                mg_bash_var=mg_bash_var,
                mg_pwsh_var=mg_pwsh_var,
                mg_note=mg_note,
            )
        )
    return steps


def _bash_head(step: _Step, verb: str) -> str:
    if step.scope == "resourceGroup":
        return f'az deployment group {verb} --resource-group "$RG_NAME"'
    if step.scope == "subscription":
        return f'az deployment sub {verb} --location "$LOCATION"'
    if step.scope == "tenant":
        return f'az deployment tenant {verb} --location "$LOCATION"'
    mg = step.mg_bash_var if step.mg_bash_var else '"$MG_ID"'
    return (
        f'az deployment mg {verb} --management-group-id {mg} '
        '--location "$LOCATION"'
    )


def _pwsh_head(step: _Step, verb: str) -> str:
    if step.scope == "resourceGroup":
        return f"az deployment group {verb} --resource-group $rgName"
    if step.scope == "subscription":
        return f"az deployment sub {verb} --location $location"
    if step.scope == "tenant":
        return f"az deployment tenant {verb} --location $location"
    mg = step.mg_pwsh_var if step.mg_pwsh_var else "$mgId"
    return (
        f"az deployment mg {verb} --management-group-id {mg} "
        "--location $location"
    )


def _render_sh(
    steps: list[_Step],
    *,
    alias_map: dict[str, str],
    tenant_id: str | None,
    resolved: _Vars,
    needs_rg: bool,
    needs_slz_root: bool,
    needs_tenant_root: bool,
    needs_generic_mg: bool,
    has_dine: bool,
) -> str:
    """Render ``deploy-all.sh``.

    ``--whatif`` (default, safe) runs what-if for every step. ``--apply``
    additionally runs create after every what-if succeeds. Fail-fast via
    ``set -euo pipefail``; first non-zero aborts.

    Variables at the top of the script are pre-filled from the scaffold
    params (``$location``, ``$tenantRootMgId``, ``$slzRootMgId``) when
    the values were derivable from findings + alias_map + run_scope —
    see :func:`_resolve_vars`. Operators no longer need to retype values
    they already accepted during the Scaffold phase; ``what-if`` is the
    review gate before ``--apply``.

    The angle-bracket fail-fast guard still runs for any variable that
    could NOT be derived (empty findings, missing alias). On Windows az
    is a .cmd wrapper and cmd.exe treats a literal ``<foo>`` as input
    redirection, which would otherwise masquerade as a phantom success.
    """
    tenant_default = tenant_id or "<tenant-id>"
    brownfield_block = _sh_brownfield_block(alias_map) if alias_map else ""
    var_lines: list[str] = [f'LOCATION="{resolved.location}"']
    placeholder_vars: list[str] = []
    if resolved.location_is_placeholder:
        placeholder_vars.append("LOCATION")
    if needs_generic_mg:
        var_lines.insert(0, 'MG_ID="<your-mg-id>"')
        placeholder_vars.insert(0, "MG_ID")
    if needs_slz_root:
        var_lines.append(f'SLZ_ROOT_MG_ID="{resolved.slz_root_mg}"')
        if resolved.slz_root_is_placeholder:
            placeholder_vars.append("SLZ_ROOT_MG_ID")
    if needs_tenant_root:
        var_lines.append(f'TENANT_ROOT_MG_ID="{resolved.tenant_root_mg}"')
        if resolved.tenant_root_is_placeholder:
            placeholder_vars.append("TENANT_ROOT_MG_ID")
    if needs_rg:
        var_lines.append('RG_NAME="<your-resource-group>"')
        placeholder_vars.append("RG_NAME")
    vars_block = "\n".join(var_lines)

    # Fail-fast only on vars that remained placeholders (nothing was
    # derivable). A fully-prefilled run emits an empty check block so
    # the operator isn't prompted to edit values that are already correct.
    check_lines = [
        f'if [[ "${{{v}}}" == "<"*">" ]]; then '
        f'echo "ERROR: {v} still holds the placeholder \\"${{{v}}}\\". '
        f'Edit the Variables block at the top of this script." >&2; exit 2; fi'
        for v in placeholder_vars
    ]
    placeholder_check_sh = "\n".join(check_lines)

    # --apply mode: split into Stage 1 (MG hierarchy + subscription-scoped)
    # and Stage 2 (everything else). A token refresh between stages ensures
    # the Azure AD token includes the newly-created MGs in its claims.
    stage1 = [(i, s) for i, s in enumerate(steps) if s.template in _STAGE1_TEMPLATES]
    stage2 = [(i, s) for i, s in enumerate(steps) if s.template not in _STAGE1_TEMPLATES]
    stage1_pairs = "\n\n".join(
        _sh_deploy_pair(s, i + 1, len(steps)) for i, s in stage1
    )
    stage2_pairs = "\n\n".join(
        _sh_deploy_pair(s, i + 1, len(steps)) for i, s in stage2
    )

    # Build the token-refresh + stage 2 block. Only emitted when stage 2
    # steps exist; otherwise the script deploys stage 1 and finishes.
    if stage2:
        refresh_and_stage2_sh = (
            "\n"
            "  # ==== Token refresh ====\n"
            "  # After creating management groups, the cached Azure AD token may not\n"
            "  # include the new MGs in its claims. ARM caches MG membership in the\n"
            "  # token; a stale token causes ManagementGroupNotFound or\n"
            "  # AuthorizationFailed on subsequent MG-scoped deployments. Logging out\n"
            "  # and back in forces a fresh token with updated claims.\n"
            "  echo \"\"\n"
            '  echo "==> Token refresh (az logout + az login)"\n'
            '  echo "    New MGs need a fresh token before Stage 2 can target them."\n'
            "  az logout 2>/dev/null || true\n"
            '  az login --tenant "$TENANT_EXPECTED"\n'
            '  echo "    Token refreshed."\n'
            "\n"
            f"  # ==== Stage 2: policies & assignments ====\n"
            "  echo \"\"\n"
            f'  echo "==> Stage 2 ({len(stage2)} template(s): policies & assignments)"\n'
            "  SECONDS=0\n"
            f"  {stage2_pairs}\n"
            "  echo \"\"\n"
            '  echo "==> Stage 2 complete (${SECONDS}s)"'
        )
    else:
        refresh_and_stage2_sh = ""

    # What-if-only mode: continue past individual failures so the operator
    # sees which steps succeed and which fail (greenfield: steps targeting
    # not-yet-created MGs are expected to fail).
    safe_whatif_steps = "\n\n".join(
        _sh_step_block_safe(s, i + 1, len(steps)) for i, s in enumerate(steps)
    )

    # Emit the "Next steps" footer as a sequence of independent ``echo``
    # statements. The prior implementation interpolated step 2 inside
    # step 1's double-quoted string, producing an unterminated literal
    # when has_dine was true ("Policy can't remediate" contained a bare
    # single quote inside the outer "..." that bash then tried to match).
    next_steps_lines = [
        'echo "  1. Observe compliance data for days-to-weeks (see how-to-deploy.md section: Observe window)."',
    ]
    if has_dine:
        next_steps_lines.append(
            'echo "  2. Run ./runbooks/grant-dine-roles.sh to grant Contributor/Reader '
            'to DINE MSIs (Azure Policy cannot remediate until this runs)."'
        )
    next_steps_lines.append(
        'echo "  3. For Wave 2 (enforce), re-scaffold with rolloutPhase=enforce params."'
    )
    next_steps_block = "\n".join(next_steps_lines)

    return f"""#!/usr/bin/env bash
# deploy-all.sh — one-shot Wave-1 (audit) deploy orchestrator
# Emitted by slz-readiness. Review before running.
#
# HITL contract: the slz-readiness plugin NEVER executes this file.
# hooks/pre_tool_use.py blocks the agent from running it via DENY regex.
# You, the operator, run this script yourself.
#
# Usage:
#   ./deploy-all.sh                          # what-if every template (continue-on-error)
#   ./deploy-all.sh --apply                  # per-step what-if then create (fail-fast)
#   ./deploy-all.sh --apply --skip-mg-prereq # bypass brownfield alias gate
#
# --apply deploys each template sequentially: what-if step N, create step N,
# then move to step N+1. This ensures management-groups (step 1) are created
# before later steps try to what-if against those MGs.
#
# Without --apply (default), every template is what-if'd with continue-on-error.
# In greenfield, steps targeting not-yet-created MGs are expected to fail; the
# summary reports which steps succeeded and which need --apply to proceed.
#
# Wave 1 (audit) only. For Wave 2 (enforce), re-scaffold with the params
# flipped to rolloutPhase=enforce AFTER observing compliance data for
# days-to-weeks. See how-to-deploy.md "Wave 2 — Enforce".
#
# DINE role grants: grant-dine-roles.sh is emitted separately (when
# applicable). Run it AFTER Wave 1 create succeeds — the principalIds
# only exist post-deploy.
set -euo pipefail

# Re-anchor to the run-root so sibling bicep/ and params/ paths resolve
# regardless of the caller's CWD. Enables ``./runbooks/deploy-all.sh``.
cd -- "$(dirname -- "$0")/.."

APPLY=false
SKIP_MG_PREREQ=false
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=true ;;
    --whatif) APPLY=false ;;
    --skip-mg-prereq) SKIP_MG_PREREQ=true ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

echo "==> Preflight"
TENANT_EXPECTED="{tenant_default}"
TENANT_CURRENT="$(az account show --query tenantId -o tsv 2>/dev/null || true)"
if [[ -z "$TENANT_CURRENT" ]]; then
  echo "ERROR: az account show failed. Run 'az login --tenant $TENANT_EXPECTED' first." >&2
  exit 2
fi
if [[ "$TENANT_EXPECTED" != "<tenant-id>" && "$TENANT_CURRENT" != "$TENANT_EXPECTED" ]]; then
  echo "ERROR: active tenant ($TENANT_CURRENT) != expected ($TENANT_EXPECTED)." >&2
  echo "       Run: az login --tenant $TENANT_EXPECTED" >&2
  exit 2
fi
echo "    active tenant: $TENANT_CURRENT"
{brownfield_block}
# ---- Variables (fill in before running) ----
{vars_block}

# Guard: the angle-bracket placeholders above must be replaced before running.
# Passing a literal ``<foo>`` to ``az`` via ``cmd.exe`` / batch wrappers causes
# the shell to interpret ``<`` as input redirection ("The system cannot find
# the file specified.") and masquerades as a phantom success.
{placeholder_check_sh}
echo ""

if [[ "$APPLY" == "true" ]]; then
  # ==== Stage 1: MG hierarchy + subscription-scoped resources ====
  echo "==> Stage 1 ({len(stage1)} template(s): MG hierarchy + subscription-scoped)"
  SECONDS=0
  {stage1_pairs}
  echo ""
  echo "==> Stage 1 complete (${{SECONDS}}s)"
{refresh_and_stage2_sh}
  echo ""
  echo "==> Wave 1 complete"
  echo "    Next steps:"
  {next_steps_block}
else
  # ---- What-if only: continue past failures, report summary ----
  # In greenfield, steps targeting not-yet-created MGs will fail; the operator
  # uses the summary to decide whether to proceed with --apply.
  set +e
  echo "==> Wave 1 what-if pass ({len(steps)} template(s))"
  WHATIF_FAILED=0
  SECONDS=0
  {safe_whatif_steps}

  echo ""
  WHATIF_OK=$(( {len(steps)} - WHATIF_FAILED ))
  echo "==> what-if pass: $WHATIF_OK/{len(steps)} succeeded, $WHATIF_FAILED failed (total: ${{SECONDS}}s)"
  if [[ $WHATIF_FAILED -gt 0 ]]; then
    echo "    Steps targeting not-yet-created management groups are expected to fail"
    echo "    in greenfield. Run with --apply to deploy in dependency order."
    exit 1
  fi
  echo "    Re-run with --apply to deploy. Review what-if output above first."
fi
"""


def _sh_brownfield_block(alias_map: dict[str, str]) -> str:
    table = "\n".join(f"    {k} -> {v}" for k, v in sorted(alias_map.items()))
    return f"""
if [[ "$SKIP_MG_PREREQ" != "true" ]]; then
  cat >&2 <<'EOF'

==> Brownfield alias map (mg_alias.json) is non-empty:
{table}

Bicep CANNOT re-parent an existing MG (the `parent` property is immutable).
Before proceeding, confirm each aliased MG already sits under the canonical
SLZ parent this deploy expects. Verify with:

  az account management-group show --name <mg-id> --expand \\
      --query properties.details.parent

If a move is required, run it FIRST (not inside this script):

  az account management-group move \\
      --group-id "<existing-mg-id>" \\
      --parent-id "<canonical-parent-mg-id>"

See how-to-deploy.md §"Prerequisites — brownfield MG moves" for the full
procedure and the parent-of reference table.

Pass --skip-mg-prereq to bypass this gate once you have verified.
EOF
  exit 2
fi
"""


def _sh_step_block(step: _Step, verb: str, idx: int, total: int) -> str:
    head = _bash_head(step, verb)
    phase_hint = f" (rolloutPhase={step.phase})" if step.phase else ""
    scope_hint = f" [{step.scope_name}]" if step.scope_name else ""
    note = f"\n# {step.mg_note}" if step.mg_note else ""
    return (
        f"echo \"--> [{idx}/{total}] {step.template}{scope_hint}{phase_hint} {verb}\"{note}\n"
        f"{head} \\\n"
        f"    --template-file {step.bicep} \\\n"
        f"    --parameters @{step.params}"
    )


def _sh_step_block_safe(step: _Step, idx: int, total: int) -> str:
    """Render a what-if step that continues on error, incrementing WHATIF_FAILED."""
    head = _bash_head(step, "what-if")
    phase_hint = f" (rolloutPhase={step.phase})" if step.phase else ""
    scope_hint = f" [{step.scope_name}]" if step.scope_name else ""
    note = f"\n# {step.mg_note}" if step.mg_note else ""
    return (
        f"echo \"--> [{idx}/{total}] {step.template}{scope_hint}{phase_hint} what-if\"{note}\n"
        f"if {head} \\\n"
        f"    --template-file {step.bicep} \\\n"
        f"    --parameters @{step.params}; then\n"
        f"  echo \"    ok: {step.template}{scope_hint}\"\n"
        f"else\n"
        f"  echo \"    FAILED: {step.template}{scope_hint} (exit $?)\" >&2\n"
        f"  WHATIF_FAILED=$((WHATIF_FAILED+1))\n"
        f"fi"
    )


def _sh_deploy_pair(step: _Step, idx: int, total: int) -> str:
    """Render a what-if + create pair for one step (used in --apply mode)."""
    whatif = _sh_step_block(step, "what-if", idx, total)
    create = _sh_step_block(step, "create", idx, total)
    return f"{whatif}\n\n{create}"


def _render_ps1(
    steps: list[_Step],
    *,
    alias_map: dict[str, str],
    tenant_id: str | None,
    resolved: _Vars,
    needs_rg: bool,
    needs_slz_root: bool,
    needs_tenant_root: bool,
    needs_generic_mg: bool,
    has_dine: bool,
) -> str:
    """Render ``deploy-all.ps1``.

    Symmetric to ``_render_sh``; see that docstring for the prefill
    rationale.
    """
    tenant_default = tenant_id or "<tenant-id>"
    brownfield_block = _ps1_brownfield_block(alias_map) if alias_map else ""
    var_lines: list[str] = [f'$location = "{resolved.location}"']
    placeholder_vars: list[str] = []
    if resolved.location_is_placeholder:
        placeholder_vars.append("location")
    if needs_generic_mg:
        var_lines.insert(0, '$mgId = "<your-mg-id>"')
        placeholder_vars.insert(0, "mgId")
    if needs_slz_root:
        var_lines.append(f'$slzRootMgId = "{resolved.slz_root_mg}"')
        if resolved.slz_root_is_placeholder:
            placeholder_vars.append("slzRootMgId")
    if needs_tenant_root:
        var_lines.append(f'$tenantRootMgId = "{resolved.tenant_root_mg}"')
        if resolved.tenant_root_is_placeholder:
            placeholder_vars.append("tenantRootMgId")
    if needs_rg:
        var_lines.append('$rgName = "<your-resource-group>"')
        placeholder_vars.append("rgName")
    vars_block = "\n".join(var_lines)

    # Fail-fast if any angle-bracket placeholder survived. On Windows,
    # az.cmd invocation via cmd.exe treats ``<`` in an argument as input
    # redirection — producing "The system cannot find the file specified."
    # which masquerades as a phantom success across all steps.
    check_lines = []
    for v in placeholder_vars:
        # Build PowerShell check line with `.format` to avoid f-string / pwsh
        # quote-nesting collisions. ``$(Get-Variable ...)`` echoes the actual
        # placeholder value so the operator sees exactly which one they missed.
        check_lines.append(
            'if (${v} -like "<*>") {{ Write-Error "{v} still holds the '  # noqa: UP032
            'placeholder \'$(Get-Variable {v} -ValueOnly)\'. Edit the '
            'Variables block at the top of this script."; exit 2 }}'.format(v=v)
        )
    placeholder_check_ps1 = "\n".join(check_lines)

    # --apply mode: split into Stage 1 and Stage 2 with token refresh
    stage1 = [(i, s) for i, s in enumerate(steps) if s.template in _STAGE1_TEMPLATES]
    stage2 = [(i, s) for i, s in enumerate(steps) if s.template not in _STAGE1_TEMPLATES]
    stage1_pairs = "\n\n".join(
        _ps1_deploy_pair(s, i + 1, len(steps)) for i, s in stage1
    )
    stage2_pairs = "\n\n".join(
        _ps1_deploy_pair(s, i + 1, len(steps)) for i, s in stage2
    )

    # Build the token-refresh + stage 2 block for PS1.
    if stage2:
        refresh_and_stage2_ps1 = (
            "\n"
            "    # ==== Token refresh ====\n"
            "    # After creating management groups, the cached Azure AD token may not\n"
            "    # include the new MGs in its claims. ARM caches MG membership in the\n"
            "    # token; a stale token causes ManagementGroupNotFound or\n"
            "    # AuthorizationFailed on subsequent MG-scoped deployments. Logging out\n"
            "    # and back in forces a fresh token with updated claims.\n"
            '    Write-Host ""\n'
            '    Write-Host "==> Token refresh (az logout + az login)"\n'
            '    Write-Host "    New MGs need a fresh token before Stage 2 can target them."\n'
            "    az logout 2>$null\n"
            "    az login --tenant $tenantExpected\n"
            '    Write-Host "    Token refreshed."\n'
            "\n"
            "    # ==== Stage 2: policies & assignments ====\n"
            '    Write-Host ""\n'
            f'    Write-Host "==> Stage 2 ({len(stage2)} template(s): policies & assignments)"\n'
            "    $swStage2 = [System.Diagnostics.Stopwatch]::StartNew()\n"
            f"    {stage2_pairs}\n"
            "    $swStage2.Stop()\n"
            '    Write-Host ""\n'
            '    Write-Host ("==> Stage 2 complete ({0}s)" -f [int]$swStage2.Elapsed.TotalSeconds)'
        )
    else:
        refresh_and_stage2_ps1 = ""

    # What-if-only mode: continue past failures, report summary
    safe_whatif_steps = "\n\n".join(
        _ps1_step_block_safe(s, i + 1, len(steps)) for i, s in enumerate(steps)
    )

    # Emit the "Next steps" footer as independent Write-Host calls. The
    # prior implementation interpolated step 2 inside step 1's double-
    # quoted string, producing an unterminated string literal when
    # has_dine was true.
    next_steps_lines = [
        'Write-Host "  1. Observe compliance data for days-to-weeks (see how-to-deploy.md Observe window section)."',
    ]
    if has_dine:
        next_steps_lines.append(
            'Write-Host "  2. Run ./runbooks/grant-dine-roles.ps1 to grant Contributor/Reader to DINE MSIs."'
        )
    next_steps_lines.append(
        'Write-Host "  3. For Wave 2 (enforce), re-scaffold with rolloutPhase=enforce params."'
    )
    next_steps_block = "\n".join(next_steps_lines)

    return f"""<#
.SYNOPSIS
    One-shot Wave-1 (audit) deploy orchestrator for slz-readiness scaffold output.

.DESCRIPTION
    With -Apply: deploys each template sequentially (what-if step N, create
    step N, then move to N+1). Management groups created by step 1 exist
    before later steps try to what-if against them.

    Without -Apply (default): runs what-if for every template with
    continue-on-error. In greenfield, steps targeting not-yet-created MGs are
    expected to fail; the summary reports which steps need -Apply to proceed.

    Wave 1 (audit) only. For Wave 2 (enforce) re-scaffold with
    `rolloutPhase=enforce` AFTER observing compliance data for
    days-to-weeks. See how-to-deploy.md "Wave 2 - Enforce".

    DINE role grants: grant-dine-roles.ps1 is emitted separately
    (when applicable). Run it AFTER Wave 1 create succeeds.

.PARAMETER Apply
    Per-step deploy: what-if then create for each template in canonical
    order (fail-fast). MGs are created before later steps need them.

.PARAMETER SkipMgPrereq
    Bypass the brownfield alias-map gate. Use only after verifying each
    aliased MG is already parented correctly.

.EXAMPLE
    ./deploy-all.ps1                 # what-if only (continue-on-error)
    ./deploy-all.ps1 -Apply          # per-step deploy Wave 1

.NOTES
    Emitted by slz-readiness. Review before running.
    The plugin never executes this file; HITL deployment is the contract
    (see how-to-deploy.md, AGENTS.md rules 1/6/7).
#>
[CmdletBinding()]
param(
    [switch] $Apply,
    [switch] $SkipMgPrereq
)

$ErrorActionPreference = 'Stop'

# Re-anchor to the run-root so sibling bicep/ and params/ paths resolve
# regardless of the caller's CWD. Enables `./runbooks/deploy-all.ps1`.
Set-Location -LiteralPath (Join-Path $PSScriptRoot '..')

Write-Host "==> Preflight"
$tenantExpected = "{tenant_default}"
try {{
    $tenantCurrent = (az account show --query tenantId -o tsv)
}} catch {{
    Write-Error "az account show failed. Run 'az login --tenant $tenantExpected' first."
    exit 2
}}
if ($tenantExpected -ne "<tenant-id>" -and $tenantCurrent -ne $tenantExpected) {{
    Write-Error "active tenant ($tenantCurrent) != expected ($tenantExpected). Run: az login --tenant $tenantExpected"
    exit 2
}}
Write-Host "    active tenant: $tenantCurrent"
{brownfield_block}
# ---- Variables (fill in before running) ----
{vars_block}

# Guard: the angle-bracket placeholders above must be replaced before running.
# On Windows az.cmd invokes cmd.exe which treats ``<`` as input redirection,
# yielding "The system cannot find the file specified." — a phantom success
# across every step if this check is not in place.
{placeholder_check_ps1}

Write-Host ""

if ($Apply) {{
    # ==== Stage 1: MG hierarchy + subscription-scoped resources ====
    Write-Host "==> Stage 1 ({len(stage1)} template(s): MG hierarchy + subscription-scoped)"
    $swStage1 = [System.Diagnostics.Stopwatch]::StartNew()
    {stage1_pairs}
    $swStage1.Stop()
    Write-Host ""
    Write-Host ("==> Stage 1 complete ({{0}}s)" -f [int]$swStage1.Elapsed.TotalSeconds)
{refresh_and_stage2_ps1}
    Write-Host ""
    Write-Host "==> Wave 1 complete"
    Write-Host "    Next steps:"
    {next_steps_block}
}} else {{
    # ---- What-if only: continue past failures, report summary ----
    # In greenfield, steps targeting not-yet-created MGs will fail; the operator
    # uses the summary to decide whether to proceed with -Apply.
    $ErrorActionPreference = 'Continue'
    Write-Host "==> Wave 1 what-if pass ({len(steps)} template(s))"
    $whatifFailed = 0
    $swWhatif = [System.Diagnostics.Stopwatch]::StartNew()
    {safe_whatif_steps}
    $swWhatif.Stop()

    Write-Host ""
    $whatifOk = {len(steps)} - $whatifFailed
    Write-Host ("==> what-if pass: $whatifOk/{len(steps)} succeeded, $whatifFailed failed (total: {{0}}s)" -f [int]$swWhatif.Elapsed.TotalSeconds)
    if ($whatifFailed -gt 0) {{
        Write-Host "    Steps targeting not-yet-created management groups are expected to fail"
        Write-Host "    in greenfield. Run with -Apply to deploy in dependency order."
        exit 1
    }}
    Write-Host "    Re-run with -Apply to deploy. Review what-if output above first."
}}
"""


def _ps1_brownfield_block(alias_map: dict[str, str]) -> str:
    table_lines = "\n".join(f'Write-Host "    {k} -> {v}"' for k, v in sorted(alias_map.items()))
    return f"""
if (-not $SkipMgPrereq) {{
    Write-Host ""
    Write-Host "==> Brownfield alias map (mg_alias.json) is non-empty:"
{table_lines}
    Write-Host ""
    Write-Host "Bicep CANNOT re-parent an existing MG (parent is immutable)."
    Write-Host "Before proceeding, confirm each aliased MG is parented correctly:"
    Write-Host "  az account management-group show --name <mg-id> --expand ``"
    Write-Host "      --query properties.details.parent"
    Write-Host ""
    Write-Host "If a move is required, run 'az account management-group move' FIRST."
    Write-Host "See how-to-deploy.md 'Prerequisites - brownfield MG moves'."
    Write-Host ""
    Write-Host "Pass -SkipMgPrereq to bypass this gate once verified."
    exit 2
}}
"""


def _ps1_step_block(step: _Step, verb: str, idx: int, total: int) -> str:
    head = _pwsh_head(step, verb)
    phase_hint = f" (rolloutPhase={step.phase})" if step.phase else ""
    scope_hint = f" [{step.scope_name}]" if step.scope_name else ""
    note = f"\n# {step.mg_note}" if step.mg_note else ""
    return (
        f"Write-Host \"--> [{idx}/{total}] {step.template}{scope_hint}{phase_hint} {verb}\"{note}\n"
        f"{head} `\n"
        f"    --template-file {step.bicep} `\n"
        f"    --parameters `@{step.params}"
    )


def _ps1_step_block_safe(step: _Step, idx: int, total: int) -> str:
    """Render a what-if step that continues on error, incrementing $whatifFailed."""
    head = _pwsh_head(step, "what-if")
    phase_hint = f" (rolloutPhase={step.phase})" if step.phase else ""
    scope_hint = f" [{step.scope_name}]" if step.scope_name else ""
    note = f"\n# {step.mg_note}" if step.mg_note else ""
    return (
        f"Write-Host \"--> [{idx}/{total}] {step.template}{scope_hint}{phase_hint} what-if\"{note}\n"
        f"try {{\n"
        f"    {head} `\n"
        f"        --template-file {step.bicep} `\n"
        f"        --parameters `@{step.params}\n"
        f"    Write-Host \"    ok: {step.template}{scope_hint}\"\n"
        f"}} catch {{\n"
        f"    Write-Host \"    FAILED: {step.template}{scope_hint} ($_)\" -ForegroundColor Red\n"
        f"    $whatifFailed++\n"
        f"}}"
    )


def _ps1_deploy_pair(step: _Step, idx: int, total: int) -> str:
    """Render a what-if + create pair for one step (used in -Apply mode)."""
    whatif = _ps1_step_block(step, "what-if", idx, total)
    create = _ps1_step_block(step, "create", idx, total)
    return f"{whatif}\n\n{create}"


def _render_dine_sh(emitted: list[dict[str, Any]]) -> str:
    """Render ``grant-dine-roles.sh`` — post-Wave-1 DINE MSI role grant skeleton.

    We cannot enumerate the assignment names here (they're embedded in the
    archetype-policies Bicep module's ``assignments`` block, not in the
    emit manifest). The script therefore ships a template-per-assignment
    block that the operator fills in by reading from
    ``az policy assignment list``. This matches the DINE recipe in
    how-to-deploy.md §"DINE remediation roles" verbatim.
    """
    archetypes = [e.get("scope") or "(default)" for e in emitted if e.get("template") == "archetype-policies"]
    archetype_list = ", ".join(sorted(set(archetypes))) or "(none)"
    return f"""#!/usr/bin/env bash
# grant-dine-roles.sh — Wave-1 post-hook: grant DINE MSIs their required roles.
#
# HITL contract: emitted by slz-readiness; the plugin never runs this file.
# Run AFTER deploy-all.sh --apply succeeds. The principalIds do not exist
# until archetype-policies is deployed.
#
# Archetype scopes with DINE assignments in this run: {archetype_list}
#
# For EACH archetype assignment emitted (see artifacts/<run>/bicep/archetype-policies-*.bicep
# for the full list), look up the principalId + grant the role(s) declared
# in the underlying policy definition's roleDefinitionIds:
#
#   MG_ID="<your-mg-id>"
#   ASSIGNMENT_NAME="<assignment-name-from-bicep>"
#   ROLE="<role-definition-id>"   # from az policy set-definition show
#
#   PRINCIPAL_ID=$(az policy assignment show \\
#       --name "$ASSIGNMENT_NAME" \\
#       --scope "/providers/Microsoft.Management/managementGroups/$MG_ID" \\
#       --query identity.principalId -o tsv)
#   az role assignment create \\
#       --assignee-object-id "$PRINCIPAL_ID" \\
#       --assignee-principal-type ServicePrincipal \\
#       --role "$ROLE" \\
#       --scope "/providers/Microsoft.Management/managementGroups/$MG_ID"
#
# Preflight: this script grants RBAC. You need User Access Administrator
# or Owner at MG scope. Verify before running.
#
# See how-to-deploy.md §"DINE remediation roles" for the full recipe.
set -euo pipefail
echo "grant-dine-roles.sh: this is a TEMPLATE. Edit per the block above." >&2
echo "It does not auto-run because assignment names + role ids must be filled in." >&2
exit 1
"""


def _render_dine_ps1(emitted: list[dict[str, Any]]) -> str:
    archetypes = [e.get("scope") or "(default)" for e in emitted if e.get("template") == "archetype-policies"]
    archetype_list = ", ".join(sorted(set(archetypes))) or "(none)"
    return f"""<#
.SYNOPSIS
    Wave-1 post-hook: grant DINE MSIs their required roles.

.DESCRIPTION
    Run AFTER deploy-all.ps1 -Apply succeeds. The principalIds do not exist
    until archetype-policies is deployed.

    Archetype scopes with DINE assignments in this run: {archetype_list}

    For EACH archetype assignment emitted (see artifacts/<run>/bicep/archetype-policies-*.bicep),
    look up the principalId and grant the role(s) declared in the underlying
    policy definition's roleDefinitionIds:

        $mgId = "<your-mg-id>"
        $assignmentName = "<assignment-name-from-bicep>"
        $role = "<role-definition-id>"   # from: az policy set-definition show

        $principalId = az policy assignment show `
            --name $assignmentName `
            --scope "/providers/Microsoft.Management/managementGroups/$mgId" `
            --query identity.principalId -o tsv
        az role assignment create `
            --assignee-object-id $principalId `
            --assignee-principal-type ServicePrincipal `
            --role $role `
            --scope "/providers/Microsoft.Management/managementGroups/$mgId"

    Preflight: this grants RBAC. You need User Access Administrator or Owner
    at MG scope.

.NOTES
    Emitted by slz-readiness. HITL contract; the plugin never runs this file.
    See how-to-deploy.md "DINE remediation roles" for the full recipe.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Write-Error "grant-dine-roles.ps1: this is a TEMPLATE. Edit per the block above. It does not auto-run because assignment names + role ids must be filled in."
exit 1
"""


def write_deploy_script(
    *,
    out_dir: Path,
    emitted: list[dict[str, Any]],
    alias_map: dict[str, str] | None = None,
    tenant_id: str | None = None,
    params_by_template: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Emit ``deploy-all.{ps1,sh}`` (+ optional DINE grant pair) into ``out_dir/runbooks/``.

    Returns the list of emitted paths (relative to ``out_dir``). Returns an
    empty list when ``emitted`` is empty (nothing to orchestrate).

    ``params_by_template`` — the merged scaffold params dict (same shape
    ``prefill_params`` returns: ``{template: {param: value, ...}}``). When
    supplied, :func:`_resolve_vars` uses it to pre-fill ``$location``,
    ``$tenantRootMgId``, and ``$slzRootMgId`` at emit time. Omitting it
    falls back to angle-bracket placeholders (preserves backwards-compat
    for direct callers and tests that don't exercise prefill).

    The agent is still blocked from executing the emitted scripts by
    ``hooks/pre_tool_use.py`` — this function writes them for the human
    operator. See module docstring for the full HITL contract.
    """
    if not emitted:
        return []
    alias_map = alias_map or {}
    steps = _plan_steps(emitted, alias_map=alias_map)
    if not steps:
        return []
    resolved = _resolve_vars(params_by_template, alias_map, tenant_id)
    needs_rg = any(s.scope == "resourceGroup" for s in steps)
    needs_slz_root = any(
        s.template in {"sovereignty-global-policies", "alz-policy-definitions"} for s in steps
    )
    needs_tenant_root = any(s.template == "management-groups" for s in steps)
    # Generic ``$mgId`` / ``$MG_ID`` is only referenced when a MG-scoped step
    # has no template-specific resolution. With per-step inlining for
    # policy-assignment + role-assignment (see _plan_steps), this is
    # typically empty in practice — the variable + guard are only emitted
    # when some step genuinely has no resolvable scope.
    needs_generic_mg = any(
        s.scope == "managementGroup" and s.mg_pwsh_var is None for s in steps
    )
    has_dine = any(s.template == "archetype-policies" for s in steps)

    runbooks_dir = out_dir / "runbooks"
    runbooks_dir.mkdir(exist_ok=True)
    written: list[str] = []

    sh = _render_sh(
        steps,
        alias_map=alias_map,
        tenant_id=tenant_id,
        resolved=resolved,
        needs_rg=needs_rg,
        needs_slz_root=needs_slz_root,
        needs_tenant_root=needs_tenant_root,
        needs_generic_mg=needs_generic_mg,
        has_dine=has_dine,
    )
    sh_path = runbooks_dir / "deploy-all.sh"
    sh_path.write_text(sh, encoding="utf-8", newline="\n")
    written.append(sh_path.relative_to(out_dir).as_posix())

    ps1 = _render_ps1(
        steps,
        alias_map=alias_map,
        tenant_id=tenant_id,
        resolved=resolved,
        needs_rg=needs_rg,
        needs_slz_root=needs_slz_root,
        needs_tenant_root=needs_tenant_root,
        needs_generic_mg=needs_generic_mg,
        has_dine=has_dine,
    )
    ps1_path = runbooks_dir / "deploy-all.ps1"
    ps1_path.write_text(ps1, encoding="utf-8", newline="\n")
    written.append(ps1_path.relative_to(out_dir).as_posix())

    if has_dine:
        dine_sh_path = runbooks_dir / "grant-dine-roles.sh"
        dine_sh_path.write_text(_render_dine_sh(emitted), encoding="utf-8", newline="\n")
        written.append(dine_sh_path.relative_to(out_dir).as_posix())
        dine_ps1_path = runbooks_dir / "grant-dine-roles.ps1"
        dine_ps1_path.write_text(_render_dine_ps1(emitted), encoding="utf-8", newline="\n")
        written.append(dine_ps1_path.relative_to(out_dir).as_posix())

    _trace.log(
        "scaffold.deploy_script",
        emitted_count=len(steps),
        has_dine=has_dine,
        brownfield=bool(alias_map),
        runbooks=written,
    )
    return written
