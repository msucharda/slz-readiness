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

from pathlib import Path
from typing import Any

from .. import _trace
from .template_registry import TEMPLATE_SCOPES

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
        elif template == "sovereignty-confidential-policies" and scope_name in alias_map:
            resolved = alias_map[scope_name]
            mg_bash_var = f'"{resolved}"'
            mg_pwsh_var = f'"{resolved}"'
            mg_note = f"target MG resolved from mg_alias.json: {scope_name} -> {resolved}"
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
    needs_rg: bool,
    needs_slz_root: bool,
    has_dine: bool,
) -> str:
    """Render ``deploy-all.sh``.

    ``--whatif`` (default, safe) runs what-if for every step. ``--apply``
    additionally runs create after every what-if succeeds. Fail-fast via
    ``set -euo pipefail``; first non-zero aborts.
    """
    tenant_default = tenant_id or "<tenant-id>"
    brownfield_block = _sh_brownfield_block(alias_map) if alias_map else ""
    var_lines = ['MG_ID="<your-mg-id>"', 'LOCATION="<your-region>"']
    if needs_slz_root:
        slz_default = alias_map.get("slz", "<your-slz-root-mg-id>")
        var_lines.append(f'SLZ_ROOT_MG_ID="{slz_default}"')
    if needs_rg:
        var_lines.append('RG_NAME="<your-resource-group>"')
    vars_block = "\n".join(var_lines)

    whatif_steps = "\n\n".join(_sh_step_block(s, "what-if", i + 1, len(steps)) for i, s in enumerate(steps))
    create_steps = "\n\n".join(_sh_step_block(s, "create", i + 1, len(steps)) for i, s in enumerate(steps))

    dine_footer = ""
    if has_dine:
        dine_footer = (
            '\n  echo "  2. Run ./grant-dine-roles.sh to grant Contributor/Reader '
            'to DINE MSIs (Azure Policy can\'t remediate until this runs)."'
        )

    return f"""#!/usr/bin/env bash
# deploy-all.sh — one-shot Wave-1 (audit) deploy orchestrator
# Emitted by slz-readiness. Review before running.
#
# HITL contract: the slz-readiness plugin NEVER executes this file.
# hooks/pre_tool_use.py blocks the agent from running it via DENY regex.
# You, the operator, run this script yourself.
#
# Usage:
#   ./deploy-all.sh                          # what-if every template (safe)
#   ./deploy-all.sh --apply                  # run create after every what-if succeeds
#   ./deploy-all.sh --apply --skip-mg-prereq # bypass brownfield alias gate
#
# Wave 1 (audit) only. For Wave 2 (enforce), re-scaffold with the params
# flipped to rolloutPhase=enforce AFTER observing compliance data for
# days-to-weeks. See how-to-deploy.md "Wave 2 — Enforce".
#
# DINE role grants: grant-dine-roles.sh is emitted separately (when
# applicable). Run it AFTER Wave 1 create succeeds — the principalIds
# only exist post-deploy.
set -euo pipefail

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

echo ""
echo "==> Wave 1 what-if pass ({len(steps)} template(s))"
SECONDS=0
{whatif_steps}

echo ""
echo "==> what-if pass complete (total: ${{SECONDS}}s)"
if [[ "$APPLY" != "true" ]]; then
  echo "    Re-run with --apply to deploy. Review what-if output above first."
  exit 0
fi

echo ""
echo "==> Wave 1 create pass"
SECONDS=0
{create_steps}

echo ""
echo "==> Wave 1 complete (total: ${{SECONDS}}s)"
echo "    Next steps:"
echo "  1. Observe compliance data for days-to-weeks (see how-to-deploy.md §Observe window).{dine_footer}"
echo "  3. For Wave 2 (enforce), re-scaffold with rolloutPhase=enforce params."
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


def _render_ps1(
    steps: list[_Step],
    *,
    alias_map: dict[str, str],
    tenant_id: str | None,
    needs_rg: bool,
    needs_slz_root: bool,
    has_dine: bool,
) -> str:
    """Render ``deploy-all.ps1``."""
    tenant_default = tenant_id or "<tenant-id>"
    brownfield_block = _ps1_brownfield_block(alias_map) if alias_map else ""
    var_lines = ['$mgId = "<your-mg-id>"', '$location = "<your-region>"']
    if needs_slz_root:
        slz_default = alias_map.get("slz", "<your-slz-root-mg-id>")
        var_lines.append(f'$slzRootMgId = "{slz_default}"')
    if needs_rg:
        var_lines.append('$rgName = "<your-resource-group>"')
    vars_block = "\n".join(var_lines)

    whatif_steps = "\n\n".join(_ps1_step_block(s, "what-if", i + 1, len(steps)) for i, s in enumerate(steps))
    create_steps = "\n\n".join(_ps1_step_block(s, "create", i + 1, len(steps)) for i, s in enumerate(steps))

    dine_footer = ""
    if has_dine:
        dine_footer = (
            "\nWrite-Host \"  2. Run ./grant-dine-roles.ps1 to grant Contributor/Reader to DINE MSIs.\""
        )

    return f"""<#
.SYNOPSIS
    One-shot Wave-1 (audit) deploy orchestrator for slz-readiness scaffold output.

.DESCRIPTION
    Iterates the emitted templates in canonical deploy order. Runs
    `az deployment ... what-if` for every template first; only runs `create`
    when -Apply is passed and every what-if succeeded.

    Wave 1 (audit) only. For Wave 2 (enforce) re-scaffold with
    `rolloutPhase=enforce` AFTER observing compliance data for
    days-to-weeks. See how-to-deploy.md "Wave 2 - Enforce".

    DINE role grants: grant-dine-roles.ps1 is emitted separately
    (when applicable). Run it AFTER Wave 1 create succeeds.

.PARAMETER Apply
    If set, runs `az deployment ... create` after every what-if succeeds.
    Default is what-if only (safe).

.PARAMETER SkipMgPrereq
    Bypass the brownfield alias-map gate. Use only after verifying each
    aliased MG is already parented correctly.

.EXAMPLE
    ./deploy-all.ps1                 # what-if only (default, safe)
    ./deploy-all.ps1 -Apply          # deploy Wave 1

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

Write-Host ""
Write-Host "==> Wave 1 what-if pass ({len(steps)} template(s))"
$swWhatif = [System.Diagnostics.Stopwatch]::StartNew()
{whatif_steps}
$swWhatif.Stop()

Write-Host ""
Write-Host ("==> what-if pass complete (total: {{0}}s)" -f [int]$swWhatif.Elapsed.TotalSeconds)
if (-not $Apply) {{
    Write-Host "    Re-run with -Apply to deploy. Review what-if output above first."
    exit 0
}}

Write-Host ""
Write-Host "==> Wave 1 create pass"
$swCreate = [System.Diagnostics.Stopwatch]::StartNew()
{create_steps}
$swCreate.Stop()

Write-Host ""
Write-Host ("==> Wave 1 complete (total: {{0}}s)" -f [int]$swCreate.Elapsed.TotalSeconds)
Write-Host "    Next steps:"
Write-Host "  1. Observe compliance data for days-to-weeks (see how-to-deploy.md Observe window section).{dine_footer}"
Write-Host "  3. For Wave 2 (enforce), re-scaffold with rolloutPhase=enforce params."
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
) -> list[str]:
    """Emit ``deploy-all.{ps1,sh}`` (+ optional DINE grant pair) into ``out_dir/runbooks/``.

    Returns the list of emitted paths (relative to ``out_dir``). Returns an
    empty list when ``emitted`` is empty (nothing to orchestrate).

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
    needs_rg = any(s.scope == "resourceGroup" for s in steps)
    needs_slz_root = any(
        s.template in {"sovereignty-global-policies", "alz-policy-definitions"} for s in steps
    )
    has_dine = any(s.template == "archetype-policies" for s in steps)

    runbooks_dir = out_dir / "runbooks"
    runbooks_dir.mkdir(exist_ok=True)
    written: list[str] = []

    sh = _render_sh(
        steps,
        alias_map=alias_map,
        tenant_id=tenant_id,
        needs_rg=needs_rg,
        needs_slz_root=needs_slz_root,
        has_dine=has_dine,
    )
    sh_path = runbooks_dir / "deploy-all.sh"
    sh_path.write_text(sh, encoding="utf-8", newline="\n")
    written.append(sh_path.relative_to(out_dir).as_posix())

    ps1 = _render_ps1(
        steps,
        alias_map=alias_map,
        tenant_id=tenant_id,
        needs_rg=needs_rg,
        needs_slz_root=needs_slz_root,
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
