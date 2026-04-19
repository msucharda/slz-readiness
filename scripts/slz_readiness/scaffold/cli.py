"""`slz-scaffold` — consumes gaps.json + params.json and emits Bicep/params files."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from .. import _summary, _trace
from .engine import ScaffoldError, scaffold_for_gaps
from .prefill import (
    classify_keys,
    merge_params,
    needs_operator_input_keys,
    prefill_params,
    strip_engine_owned_fields,
)
from .template_registry import INFORMATIONAL_RULES, RULE_TO_TEMPLATE, TEMPLATE_SCOPES

# Human-readable order the deployment block recommends.
_DEPLOY_ORDER: list[str] = [
    "management-groups",
    "log-analytics",
    "sovereignty-global-policies",
    "archetype-policies",
    "sovereignty-confidential-policies",
    "policy-assignment",
    "role-assignment",
]


def _unscaffolded_gaps(
    gaps: list[dict[str, Any]],
    emitted: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return gaps the scaffold engine did NOT emit Bicep for.

    v0.11.0 — now derives the truth from actual emissions, not just template
    registry membership. The registry says *which* template *would* cover a
    rule; the emit path may still abort (placeholder-only assignments,
    no-resolvable-assignments, unknown discovery status, etc.) and those
    gaps used to be silently marked as scaffolded. See slz-demo run
    20260419T120215Z (finding C1).

    Reason codes (preserved in the JSON summary):

    * ``"unknown"`` — discovery couldn't verify; cannot scaffold what we
      can't verify.
    * ``"informational"`` — rule is in :data:`INFORMATIONAL_RULES` (drift
      detection only, scaffold intentionally does not auto-remediate).
    * ``"no_template"`` — no ``RULE_TO_TEMPLATE`` entry for this rule.
    * ``"emit_skipped"`` — rule maps to a template but no Bicep was
      emitted for it (template-level abort — see scaffold warnings /
      trace.jsonl for the per-template reason).
    """
    emitted_rule_ids: set[str] = {
        rid
        for e in (emitted or [])
        for rid in (e.get("rule_ids") or [])
    }
    out: list[dict[str, Any]] = []
    for g in gaps:
        rule_id = g.get("rule_id", "")
        status = g.get("status", "missing")
        if status == "unknown":
            out.append({**g, "_reason": "unknown"})
            continue
        if rule_id in INFORMATIONAL_RULES:
            out.append({**g, "_reason": "informational"})
            continue
        if rule_id not in RULE_TO_TEMPLATE:
            out.append({**g, "_reason": "no_template"})
            continue
        if rule_id not in emitted_rule_ids:
            out.append({**g, "_reason": "emit_skipped"})
    out.sort(key=lambda g: (g.get("rule_id", ""), g.get("resource_id", "")))
    return out


def _deploy_commands(
    emitted: list[dict[str, Any]],
    *,
    tenant_id: str | None = None,
    alias_map: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Build the ``what-if`` + ``create`` command blocks in both Bash and PowerShell.

    Returns a dict with keys ``bash`` and ``pwsh``. Both forms use ``az`` CLI
    (semantically identical) — the only differences are line-continuation syntax
    (``\\`` vs backtick) and variable reference style (``$MG_ID`` vs ``$mgId``).
    The scaffolding operator and the deploying operator may run in different
    shells, so both are always emitted.

    Commands are **scope-aware**: each template's ``targetScope`` is read from
    ``TEMPLATE_SCOPES`` and the matching ``az deployment {mg|group|tenant}``
    verb is emitted. MG- and tenant-scoped deployments also emit the
    mandatory ``--location`` flag (ARM requires it for deployment metadata).
    Mixing these up produces *"target scope X does not match deployment
    scope Y"* at ``what-if`` time — this function's reason for existing.

    ``alias_map`` (when provided) overrides the target MG id for two
    templates whose canonical scope is NOT the landing-zone MG and is
    NOT the tenant root:

    * ``sovereignty-global-policies`` -> the ``slz`` alias (sovereign
      root MG). Without an alias, defaults to ``$MG_ID`` with a loud
      reminder, NOT the tenant GUID — the previous default silently
      mis-scoped policies one level too high. See slz-demo run
      20260419T120215Z (finding C3).
    * ``sovereignty-confidential-policies`` emissions carry a
      per-scope hint (``scope=confidential_corp`` or
      ``confidential_online``) that resolves to the matching alias
      entry so each confidential archetype lands on the correct MG
      (finding H1).

    ``tenant_id`` is retained for backwards compatibility with older
    callers; it is no longer used for sovereign-root targeting because
    the sovereign root is a *child* of the tenant root, not the tenant
    root itself.
    """
    alias_map = alias_map or {}
    by_order = sorted(
        emitted,
        key=lambda e: (
            _DEPLOY_ORDER.index(e["template"]) if e["template"] in _DEPLOY_ORDER else 99,
            e.get("scope", ""),
        ),
    )
    needs_rg = any(
        TEMPLATE_SCOPES.get(e.get("template", ""), "managementGroup") == "resourceGroup"
        for e in emitted
    )
    slz_alias = alias_map.get("slz")
    needs_slz_root = any(
        e.get("template") == "sovereignty-global-policies" for e in emitted
    )
    bash_lines: list[str] = ['MG_ID="<your-mg-id>"', 'LOCATION="<your-region>"']
    pwsh_lines: list[str] = ['$mgId = "<your-mg-id>"', '$location = "<your-region>"']
    if needs_slz_root:
        slz_default = slz_alias or "<your-slz-root-mg-id>"
        bash_lines.append(f'SLZ_ROOT_MG_ID="{slz_default}"')
        pwsh_lines.append(f'$slzRootMgId = "{slz_default}"')
    if needs_rg:
        bash_lines.append('RG_NAME="<your-resource-group>"')
        pwsh_lines.append('$rgName = "<your-resource-group>"')
    bash_lines.append("")
    pwsh_lines.append("")

    # Unused-arg guard (kept for backcompat); explicit to satisfy linters.
    _ = tenant_id

    for e in by_order:
        template = e.get("template", "")
        bicep = e.get("bicep", "")
        params = e.get("params", "")
        phase = e.get("rollout_phase")
        phase_hint = f" (rolloutPhase={phase})" if phase else ""
        scope_name = e.get("scope") or ""
        scope = TEMPLATE_SCOPES.get(template, "managementGroup")

        # Per-template MG-id variable override. For sovereignty-* templates
        # we bind to a role-specific MG rather than the generic ``$MG_ID``.
        mg_bash_var: str | None = None
        mg_pwsh_var: str | None = None
        mg_note: str | None = None
        if template == "sovereignty-global-policies":
            mg_bash_var = "$SLZ_ROOT_MG_ID"
            mg_pwsh_var = "$slzRootMgId"
            if not slz_alias:
                mg_note = (
                    "# NOTE: no mg_alias.json entry for `slz`; SLZ_ROOT_MG_ID "
                    "defaults to a placeholder. Populate it with your "
                    "sovereign-root MG id (NOT the tenant root)."
                )
        elif template == "sovereignty-confidential-policies" and scope_name in alias_map:
            # The alias value is the customer's actual MG name for this
            # confidential archetype; inline it so the operator doesn't
            # have to remember which `$MG_ID` value to reuse per deploy.
            resolved = alias_map[scope_name]
            mg_bash_var = f'"{resolved}"'
            mg_pwsh_var = f'"{resolved}"'
            mg_note = f"# target MG resolved from mg_alias.json: {scope_name} -> {resolved}"

        if scope == "resourceGroup":
            bash_head = 'az deployment group {verb} --resource-group "$RG_NAME"'
            pwsh_head = "az deployment group {verb} --resource-group $rgName"
        elif scope == "subscription":
            bash_head = 'az deployment sub {verb} --location "$LOCATION"'
            pwsh_head = "az deployment sub {verb} --location $location"
        elif scope == "tenant":
            bash_head = 'az deployment tenant {verb} --location "$LOCATION"'
            pwsh_head = "az deployment tenant {verb} --location $location"
        else:  # managementGroup (default)
            bash_mg = mg_bash_var if mg_bash_var else '"$MG_ID"'
            pwsh_mg = mg_pwsh_var if mg_pwsh_var else "$mgId"
            bash_head = (
                f'az deployment mg {{verb}} --management-group-id {bash_mg} '
                '--location "$LOCATION"'
            )
            pwsh_head = (
                f"az deployment mg {{verb}} --management-group-id {pwsh_mg} "
                "--location $location"
            )

        bash_lines.append(f"# {template}{phase_hint} — what-if first, then create")
        if mg_note:
            bash_lines.append(mg_note)
        for verb in ("what-if", "create"):
            bash_lines.append(
                f"{bash_head.format(verb=verb)} \\\n"
                f"    --template-file {bicep} \\\n"
                f"    --parameters @{params}"
            )
        bash_lines.append("")

        pwsh_lines.append(f"# {template}{phase_hint} — what-if first, then create")
        if mg_note:
            pwsh_lines.append(mg_note)
        for verb in ("what-if", "create"):
            pwsh_lines.append(
                f"{pwsh_head.format(verb=verb)} `\n"
                f"    --template-file {bicep} `\n"
                f"    --parameters `@{params}"
            )
        pwsh_lines.append("")
    return {"bash": bash_lines, "pwsh": pwsh_lines}


def _load_alias_for_doc(run_dir: Path | None) -> dict[str, str]:
    """Re-read ``mg_alias.json`` for how-to-deploy.md emission.

    Thin wrapper around :func:`slz_readiness._alias_io.load_alias_map`
    with tracing suppressed (the doc emitter is read-only and shouldn't
    pollute the trace log with a second ``alias.loaded`` entry already
    emitted by the scaffold engine on the same run).
    """
    from .._alias_io import load_alias_map
    return load_alias_map(run_dir, trace_label=None)


def _write_how_to_deploy(
    *,
    out_dir: Path,
    emitted: list[dict[str, Any]],
    run_dir: Path | None = None,
    rewrite_names: bool = False,
    tenant_id: str | None = None,
) -> None:
    """Emit ``how-to-deploy.md`` with Wave-1/Wave-2 recipes in Bash + PowerShell.

    Required by operating instructions §5. The scaffold phase never runs the
    deployment — this file is the HITL hand-off to the human operator.

    When ``rewrite_names=True`` and ``mg_alias.json`` is non-empty, the
    emitted Bicep already carries tenant MG names; the alias table is
    replaced with a short "apply-ready" note.

    ``tenant_id`` is accepted for backwards compatibility but is **not**
    used for sovereignty-global-policies targeting: the sovereign-root
    MG is a *child* of the tenant root, so binding there silently
    over-scopes the policy assignment (slz-demo run 20260419T120215Z,
    finding C3). The sovereign-root target is resolved from the ``slz``
    entry of ``mg_alias.json`` via ``_load_alias_for_doc(run_dir)``.
    """
    if not emitted:
        return
    # v0.11.0 — pass the alias map through so sovereignty-* templates
    # bind to the correct MG (slz root + confidential_corp/online)
    # rather than the tenant root or a generic $MG_ID. Without this,
    # sovereignty-global-policies silently landed two levels too high.
    # See slz-demo run 20260419T120215Z (findings C3 + H1).
    alias_map = _load_alias_for_doc(run_dir)
    cmds = _deploy_commands(emitted, tenant_id=tenant_id, alias_map=alias_map)
    has_dine = any(e.get("template") in {"archetype-policies"} for e in emitted)
    has_phased = any(e.get("rollout_phase") for e in emitted)
    needs_rg = any(
        TEMPLATE_SCOPES.get(e.get("template", ""), "managementGroup") == "resourceGroup"
        for e in emitted
    )
    needs_slz_root = any(
        e.get("template") == "sovereignty-global-policies" for e in emitted
    )
    slz_alias = alias_map.get("slz")
    slz_root_default = slz_alias or "<your-slz-root-mg-id>"
    mg_runbooks = [
        rb
        for e in emitted
        if e.get("template") == "management-groups"
        for rb in e.get("runbooks", []) or []
    ]

    parts: list[str] = []
    parts.append(
        _summary.header_block(
            "SLZ Scaffold — how to deploy",
            run_id=_summary.run_id_from_path(out_dir),
        )
    )
    parts.append(
        "This file is the human hand-off for deploying the Bicep scaffolded by "
        "`slz-scaffold`. The agent never runs `az deployment create`; always "
        "run `what-if` first and review the result before `create`."
    )
    parts.append("")

    # v0.7.0 brownfield retargeting block — emitted only when a non-empty
    # ``mg_alias.json`` was loaded by the engine. Tells the operator which
    # ``MG_ID`` value to substitute per template scope so per-archetype
    # deployments hit the customer's actual MG, not the canonical SLZ name.
    # (``alias_map`` was loaded at the top of this function for the deploy
    # commands; reused here for the table-of-aliases section.)
    if alias_map:
        # v0.9.0 brownfield MG move prerequisite: Bicep cannot re-parent
        # existing MGs; operators must run ``az account management-group
        # move`` manually BEFORE ``az deployment mg create`` for each
        # aliased role. We emit this block whenever a non-empty alias
        # map is present — regardless of --rewrite-names — because the
        # move step is orthogonal to name substitution.
        parts.append("## Prerequisites — brownfield MG moves (read FIRST)")
        parts.append("")
        parts.append(
            "Your `mg_alias.json` maps canonical SLZ roles onto existing "
            "management groups in your tenant. **Bicep cannot re-parent an "
            "existing MG** (the `Microsoft.Management/managementGroups` "
            "resource's `parent` property is immutable once the MG has been "
            "created). Before running any `az deployment mg create` below, "
            "confirm each aliased MG sits under the canonical parent the "
            "template expects. If it does not, run the move explicitly:"
        )
        parts.append("")
        parts.append("```bash")
        parts.append(
            "# For each row in the alias table below whose real parent does"
        )
        parts.append(
            "# NOT already equal the canonical parent the template expects:"
        )
        parts.append(
            'az account management-group move \\'
        )
        parts.append(
            '  --group-id "<existing-mg-id>" \\'
        )
        parts.append(
            '  --parent-id "<canonical-parent-mg-id>"'
        )
        parts.append("```")
        parts.append("")
        parts.append(
            "Verify with `az account management-group show --name "
            "<existing-mg-id> --expand --query properties.details.parent` "
            "before proceeding. Skipping this step causes `az deployment "
            "mg create` to silently bind policies/role-assignments at the "
            "wrong scope (they stick to the MG's existing parent chain)."
        )
        parts.append("")
        parts.append(
            "**Detailed procedure:** see "
            "[`scripts/scaffold/runbooks/brownfield-mg-reparent.md`]"
            "(../../scripts/scaffold/runbooks/brownfield-mg-reparent.md) "
            "for the canonical parent-of table and a step-by-step "
            "move-then-deploy recipe. It also documents "
            "`ParentManagementGroupCannotBeChanged`, the specific failure "
            "mode a naïve deploy hits in brownfield tenants."
        )
        parts.append("")
    if alias_map and rewrite_names:
        parts.append("## Brownfield retargeting (applied — apply-ready Bicep)")
        parts.append("")
        parts.append(
            "`slz-scaffold --rewrite-names` was used, so the emitted Bicep "
            "already carries your tenant's MG names — no manual substitution "
            "required. Use the tenant MG names directly when filling "
            "`MG_ID` below."
        )
        parts.append("")
        parts.append(
            _summary.render_table(
                ["Canonical role", "Your MG name (now in Bicep)"],
                [[k, v] for k, v in sorted(alias_map.items())],
            )
        )
        parts.append("")
    elif alias_map:
        parts.append("## Brownfield retargeting (mg_alias.json)")
        parts.append("")
        parts.append(
            "Your `mg_alias.json` maps canonical SLZ roles to your tenant's "
            "actual management-group names. When the deploy commands below "
            "ask for `MG_ID` / `<your-mg-id>`, use the **right-hand value** "
            "for each template's role. Per-archetype templates "
            "(`archetype-policies-<role>.bicep`, `sovereignty-confidential-policies-<role>.bicep`) "
            "are deployed once per scope — pick the MG accordingly."
        )
        parts.append("")
        parts.append(
            _summary.render_table(
                ["Canonical role", "Your MG name"],
                [[k, v] for k, v in sorted(alias_map.items())],
            )
        )
        parts.append("")
        parts.append(
            "Roles not listed above are still treated as their canonical "
            "SLZ names. Re-run `slz-reconcile` to update the mapping."
        )
        parts.append("")

    parts.append("## Two knobs, two different jobs")
    parts.append("")
    parts.append(
        "| Knob | Values | What it does |\n"
        "|---|---|---|\n"
        "| `rolloutPhase` | `audit` / `enforce` | Controls each policy's **effect**. "
        "`audit` logs non-compliance without blocking (Wave 1). `enforce` flips "
        "`Deny` on (Wave 2). This is the phased-rollout knob. |\n"
        "| `enforcementMode` | `Default` / `DoNotEnforce` | Azure Policy's own "
        "binary on/off switch. `DoNotEnforce` **suppresses compliance recording** "
        "— use only as an emergency kill-switch for a specific assignment. |"
    )
    parts.append("")
    parts.append(
        "The default params set `rolloutPhase=audit`. Operators must explicitly "
        "opt into `enforce` after observing compliance data."
    )
    parts.append("")
    parts.append("## Required operator permissions")
    parts.append("")
    parts.append(
        "The human who runs `az deployment … create` needs the following "
        "Azure roles at the target scope (management group / subscription):"
    )
    parts.append("")
    parts.append(
        "- **`Owner`** at the target MG/subscription **or** the combination "
        "**`Contributor`** + **`User Access Administrator`**. The role-assignment "
        "capability is required because policy assignments with "
        "`identityRequired=true` create a system-assigned identity and "
        "`az role assignment create` is invoked during the DINE-remediation-roles "
        "step below.\n"
        "- **`Microsoft.Network` resource provider registered** on each subscription "
        "where the DDoS / VNET policies will evaluate. Run "
        "`az provider register --namespace Microsoft.Network` once per "
        "subscription if you see `RegisterResourceProvider` errors during "
        "`what-if`.\n"
        "- For tenant-scope deployments, **`Management Group Contributor`** at "
        "the tenant root group."
    )
    parts.append("")
    parts.append("## Deploy order (do not skip)")
    parts.append("")
    parts.append(
        "These templates have implicit dependencies. Deploy them in this "
        "order, running `what-if` before each `create`:"
    )
    parts.append("")
    # Build deploy-order list from what was actually emitted. Avoids
    # referencing templates that weren't produced (e.g. the log-analytics
    # bullet when the LA workspace already exists and the rule passed).
    _order_bullets: list[str] = []
    _idx = 1
    emitted_stems = {e.get("template") for e in emitted}
    if "management-groups" in emitted_stems:
        _order_bullets.append(
            f"{_idx}. **`management-groups`** — creates the MG tree each "
            "subsequent assignment references. Skip if your MG hierarchy "
            "already matches `data/baseline/alz-library`."
        )
        _idx += 1
    if "log-analytics" in emitted_stems:
        _order_bullets.append(
            f"{_idx}. **`log-analytics`** (subscription scope) — creates the "
            "`rg-slz-management` resource group *and* the workspace. Deploy "
            "before any policy that references the workspace id."
        )
        _idx += 1
    if emitted_stems & {"archetype-policies", "sovereignty-global-policies",
                        "sovereignty-confidential-policies"}:
        _prereq = []
        if "management-groups" in emitted_stems:
            _prereq.append("management-groups")
        if "log-analytics" in emitted_stems:
            _prereq.append("log-analytics")
        _prereq_clause = (
            f" Deploy *after* {' and '.join(_prereq)}, otherwise the "
            "`policyDefinitionId` / `workspaceResourceId` references will "
            "fail validation."
            if _prereq
            else ""
        )
        _order_bullets.append(
            f"{_idx}. **`archetype-policies`** / **`sovereignty-*-policies`** "
            f"(MG scope) — policy assignments.{_prereq_clause}"
        )
    if _order_bullets:
        parts.append("\n".join(_order_bullets))
    else:
        parts.append(
            "No templates emitted for this run — nothing to deploy. "
            "See `scaffold.summary.md` for skip reasons."
        )
    parts.append("")
    parts.append("## Prerequisites")
    parts.append("")
    parts.append(
        "`az deployment mg` and `az deployment tenant` both require "
        "`--location` (it designates where deployment metadata is stored — "
        "not where the resources live). `az deployment group` inherits "
        "location from the resource group and needs `--resource-group`."
    )
    parts.append("")
    parts.append("```powershell")
    parts.append("# PowerShell")
    parts.append("az login --tenant <tenant-id>")
    parts.append("az account set --subscription <subscription-id>")
    parts.append("$mgId = \"<your-mg-id>\"")
    parts.append("$location = \"<your-region>\"  # e.g. westeurope")
    if needs_slz_root:
        parts.append(
            f"$slzRootMgId = \"{slz_root_default}\""
            "  # SLZ-root MG id (alias `slz`) — NOT the tenant root"
        )
    if needs_rg:
        parts.append("$rgName = \"<your-resource-group>\"")
    parts.append("```")
    parts.append("")
    parts.append("```bash")
    parts.append("# Bash")
    parts.append("az login --tenant <tenant-id>")
    parts.append("az account set --subscription <subscription-id>")
    parts.append("MG_ID=\"<your-mg-id>\"")
    parts.append("LOCATION=\"<your-region>\"  # e.g. westeurope")
    if needs_slz_root:
        parts.append(
            f"SLZ_ROOT_MG_ID=\"{slz_root_default}\""
            "  # SLZ-root MG id (alias `slz`) — NOT the tenant root"
        )
    if needs_rg:
        parts.append("RG_NAME=\"<your-resource-group>\"")
    parts.append("```")
    parts.append("")
    parts.append("## Wave 1 — Audit")
    parts.append("")
    parts.append(
        "Run `what-if` for every template before `create`. Every template below "
        "is already parameterised with `rolloutPhase=audit`, so policies will "
        "**log** non-compliance without blocking."
    )
    parts.append("")
    parts.append("### PowerShell")
    parts.append("")
    parts.append("```powershell")
    parts.extend(line.rstrip() for line in cmds["pwsh"])
    parts.append("```")
    parts.append("")
    parts.append("### Bash")
    parts.append("")
    parts.append("```bash")
    parts.extend(line.rstrip() for line in cmds["bash"])
    parts.append("```")
    parts.append("")
    parts.append("## Observe window")
    parts.append("")
    parts.append(
        "Let the Audit assignments run long enough to generate compliance data "
        "(typically a full change-management cycle — days, not hours). Sample "
        "queries:"
    )
    parts.append("")
    parts.append("```powershell")
    parts.append("# PowerShell — list top non-compliant resources for the Global policy set")
    if needs_slz_root:
        parts.append("az policy state list --management-group $slzRootMgId `")
    else:
        parts.append("az policy state list --management-group $mgId `")
    parts.append(
        "    --filter \"PolicyAssignmentName eq 'Enforce-Sovereign-Global'"
        " and ComplianceState eq 'NonCompliant'\" `"
    )
    parts.append("    --top 50")
    parts.append("```")
    parts.append("")
    parts.append("```bash")
    parts.append("# Bash")
    if needs_slz_root:
        parts.append("az policy state list --management-group \"$SLZ_ROOT_MG_ID\" \\")
    else:
        parts.append("az policy state list --management-group \"$MG_ID\" \\")
    parts.append(
        "    --filter \"PolicyAssignmentName eq 'Enforce-Sovereign-Global'"
        " and ComplianceState eq 'NonCompliant'\" \\"
    )
    parts.append("    --top 50")
    parts.append("```")
    parts.append("")
    if has_phased:
        parts.append("## Wave 2 — Enforce")
        parts.append("")
        parts.append(
            "Only after the Audit wave produces clean (or explicitly-accepted) "
            "compliance data. Edit each `params/*.parameters.json` and flip "
            "`rolloutPhase` from `audit` to `enforce`, **or** re-run `slz-scaffold` "
            "with `rolloutPhase=enforce` in your params input. Then re-run the "
            "`what-if` + `create` sequence above. `what-if` will show every "
            "resource that will be blocked once enforcement is active — review "
            "carefully before `create`."
        )
        parts.append("")
        parts.append("## Rollback")
        parts.append("")
        parts.append(
            "- **Preferred**: flip `rolloutPhase` back to `audit` and redeploy. "
            "The Audit wave keeps compliance telemetry flowing.\n"
            "- **Emergency kill-switch**: set `enforcementMode=DoNotEnforce` on "
            "the affected assignment. This stops the policy from evaluating "
            "entirely — compliance data will stop, too — so use only while "
            "triaging a production incident."
        )
        parts.append("")
    if has_dine:
        parts.append("## DINE remediation roles")
        parts.append("")
        parts.append(
            "Archetype assignments with `identityRequired=true` were scaffolded "
            "with a system-assigned identity. Azure Policy cannot remediate "
            "until that identity has been granted the roles declared in the "
            "underlying policy definition's `roleDefinitionIds`. The "
            "`principalId` is only available **after** the assignment is "
            "created, so this must be a two-step deployment:"
        )
        parts.append("")
        parts.append(
            "1. Deploy the archetype-policies template (above) to create the "
            "assignment and its identity.\n"
            "2. Read the new `principalId` from each assignment and grant it "
            "the required role(s) at the target MG scope. Example, for a "
            "single assignment:"
        )
        parts.append("")
        parts.append("```powershell")
        parts.append("# PowerShell")
        parts.append("$principalId = az policy assignment show `")
        parts.append(
            "    --name <AssignmentName>"
            " --scope \"/providers/Microsoft.Management/managementGroups/$mgId\" `"
        )
        parts.append("    --query identity.principalId -o tsv")
        parts.append("az role assignment create --assignee-object-id $principalId `")
        parts.append("    --assignee-principal-type ServicePrincipal `")
        parts.append(
            "    --role <role-definition-id>"
            " --scope \"/providers/Microsoft.Management/managementGroups/$mgId\""
        )
        parts.append("```")
        parts.append("")
        parts.append("```bash")
        parts.append("# Bash")
        parts.append("PRINCIPAL_ID=$(az policy assignment show \\")
        parts.append(
            "    --name <AssignmentName>"
            " --scope \"/providers/Microsoft.Management/managementGroups/$MG_ID\" \\"
        )
        parts.append("    --query identity.principalId -o tsv)")
        parts.append("az role assignment create --assignee-object-id \"$PRINCIPAL_ID\" \\")
        parts.append("    --assignee-principal-type ServicePrincipal \\")
        parts.append(
            "    --role <role-definition-id>"
            " --scope \"/providers/Microsoft.Management/managementGroups/$MG_ID\""
        )
        parts.append("```")
        parts.append("")
        parts.append(
            "The exact `roleDefinitionIds` are declared inside the underlying "
            "policy definition — look them up with `az policy set-definition "
            "show --name <policy-set-id>` and filter on each definition's "
            "`roleDefinitionIds` array."
        )
        parts.append("")
    if mg_runbooks:
        parts.append("## When you lack tenant-scope deploy rights")
        parts.append("")
        parts.append(
            "ARM tenant-scope deployment (`az deployment tenant create`) "
            "requires `Microsoft.Resources/deployments/whatIf/action` + "
            "`.../write` at scope `/`. Enterprise principals (notably MCAPS / "
            "Microsoft-internal accounts) frequently hold `Owner` only at the "
            "tenant-root **management group**, not at `/`, so `whatIf` / "
            "`create` returns `AuthorizationFailed` even though the downstream "
            "`Microsoft.Management/managementGroups/write` calls would succeed."
        )
        parts.append("")
        parts.append(
            "If you see `AuthorizationFailed` on `az deployment tenant what-if` "
            "for `management-groups.bicep`, run one of the emitted runbooks "
            "instead. They PUT each MG resource directly at MG scope — this "
            "only requires `Microsoft.Management/managementGroups/write` at "
            "the **parent MG** (granted by `Management Group Contributor` or "
            "`Owner` at MG scope):"
        )
        parts.append("")
        for rb in mg_runbooks:
            parts.append(f"- `{rb}`")
        parts.append("")
        parts.append("```powershell")
        parts.append("# PowerShell — review the script first, then:")
        parts.append(
            "./runbooks/deploy-mg-hierarchy-lowpriv.ps1 `\n"
            "    -TenantId <tenant-id> `\n"
            "    -ParentManagementGroupId <tenant-root-mg-id> `\n"
            "    -WhatIf    # drop -WhatIf to actually create"
        )
        parts.append("```")
        parts.append("")
        parts.append("```bash")
        parts.append("# Bash")
        parts.append(
            "./runbooks/deploy-mg-hierarchy-lowpriv.sh \\\n"
            "    --tenant-id <tenant-id> \\\n"
            "    --parent-mg-id <tenant-root-mg-id> \\\n"
            "    --whatif    # drop --whatif to actually create"
        )
        parts.append("```")
        parts.append("")
        parts.append(
            "After the runbook succeeds, continue with `log-analytics` and the "
            "policy templates normally — those deploy at subscription / MG "
            "scope which your existing RBAC already covers."
        )
        parts.append("")
    parts.append("## See also")
    parts.append("")
    parts.append("- `scaffold.summary.md` — human-readable emit summary")
    parts.append("- `scaffold.manifest.json` — machine-readable emit manifest")
    parts.append("- `trace.jsonl` — every `template.emit` event with `rollout_phase`")
    _summary.write_md(out_dir / "how-to-deploy.md", "\n".join(parts))
    _trace.log("scaffold.how_to_deploy", emitted_count=len(emitted))


def _write_scaffold_summary(
    *,
    out_dir: Path,
    gaps: list[dict[str, Any]],
    emitted: list[dict[str, Any]],
    warnings: list[str],
    run_dir: Path | None = None,
) -> None:
    unscaffolded = _unscaffolded_gaps(gaps, emitted=emitted)
    alias_map = _load_alias_for_doc(run_dir)
    payload = {
        "phase": "scaffold",
        "gap_count": len(gaps),
        "emitted_count": len(emitted),
        "warning_count": len(warnings),
        "emitted": emitted,
        "warnings": warnings,
        "unscaffolded": [
            {
                "rule_id": g.get("rule_id"),
                "resource_id": g.get("resource_id"),
                "status": g.get("status"),
                "reason": g.get("_reason"),
            }
            for g in unscaffolded
        ],
    }
    _summary.write_json(out_dir / "scaffold.summary.json", payload)

    parts: list[str] = []
    parts.append(
        _summary.header_block(
            "SLZ Scaffold summary",
            run_id=_summary.run_id_from_path(out_dir),
        )
    )
    parts.append(
        f"**Emitted:** {len(emitted)} template(s). "
        f"**Warnings:** {len(warnings)}. "
        f"**Unscaffolded:** {len(unscaffolded)} gap(s)."
    )
    parts.append("")
    parts.append("## Emitted templates")
    parts.append("")
    parts.append(
        _summary.render_table(
            ["Template", "Scope", "Rules closed", "Bicep", "Params"],
            [
                [
                    e.get("template", ""),
                    e.get("scope", ""),
                    ", ".join(e.get("rule_ids") or []),
                    e.get("bicep", ""),
                    e.get("params", ""),
                ]
                for e in emitted
            ],
        )
        if emitted
        else "(none)"
    )
    parts.append("")
    if warnings:
        parts.append("## Warnings")
        parts.append("")
        for w in warnings:
            parts.append(f"- {w}")
        parts.append("")
    if unscaffolded:
        parts.append("## Gaps NOT scaffolded")
        parts.append("")
        parts.append(
            "These gaps did not produce Bicep output. Reason codes: "
            "``unknown`` — discovery couldn't verify; ``informational`` — "
            "drift-detection rule with no auto-remediation; ``no_template`` "
            "— no entry in ``template_registry.RULE_TO_TEMPLATE``; "
            "``emit_skipped`` — rule maps to a template but scaffold "
            "aborted the emit (see scaffold warnings + ``trace.jsonl`` for "
            "the per-template reason)."
        )
        parts.append("")
        parts.append(
            _summary.render_table(
                ["rule_id", "resource_id", "Reason", "Status"],
                [
                    [
                        g.get("rule_id", ""),
                        g.get("resource_id", ""),
                        g.get("_reason", ""),
                        g.get("status", ""),
                    ]
                    for g in unscaffolded
                ],
            )
        )
        parts.append("")
    if emitted:
        parts.append("## Deployment commands")
        parts.append("")
        parts.append(
            "Run `what-if` for every template before `create`. Replace "
            "`<your-mg-id>` with the target management-group id. **See "
            "`how-to-deploy.md` for the full phased-rollout recipe** "
            "(Audit → Observe → Enforce) and DINE remediation role steps."
        )
        parts.append("")
        cmds = _deploy_commands(emitted, alias_map=alias_map)
        parts.append("### PowerShell")
        parts.append("")
        parts.append("```powershell")
        parts.extend(line.rstrip() for line in cmds["pwsh"])
        parts.append("```")
        parts.append("")
        parts.append("### Bash")
        parts.append("")
        parts.append("```bash")
        parts.extend(line.rstrip() for line in cmds["bash"])
        parts.append("```")
        parts.append("")
    parts.append("## See also")
    parts.append("")
    parts.append("- `how-to-deploy.md` -- phased rollout recipe (Audit → Enforce) + DINE roles")
    parts.append("- `scaffold.manifest.json` -- machine-readable emit manifest")
    parts.append("- `bicep/` / `params/` -- generated files")
    parts.append("- `trace.jsonl` -- `template.emit` events")
    _summary.write_md(out_dir / "scaffold.summary.md", "\n".join(parts))
    _trace.log(
        "scaffold.summary",
        emitted_count=len(emitted),
        warning_count=len(warnings),
        unscaffolded_count=len(unscaffolded),
    )


def _write_run_rollup(out_dir: Path) -> None:
    """Concatenate available phase summaries into ``run.summary.md``.

    Silently skips phases whose summary file is absent (e.g. a fresh run that
    only reached Discover). Idempotent — overwrites on re-run.
    """
    # Canonical five-phase pipeline. Reconcile was added in v0.7.0 for
    # brownfield tenants; leaving it out of this list silently dropped the
    # reconcile.summary.md from run.summary.md regardless of whether it
    # existed on disk. See slz-demo run 20260419T120215Z.
    sections = [
        ("discover.summary.md", "Discover"),
        ("reconcile.summary.md", "Reconcile"),
        ("evaluate.summary.md", "Evaluate"),
        ("plan.summary.md", "Plan"),
        ("scaffold.summary.md", "Scaffold"),
    ]
    present = [(f, label) for f, label in sections if (out_dir / f).exists()]
    if not present:
        return
    parts: list[str] = []
    parts.append(
        _summary.header_block(
            "SLZ Run summary",
            run_id=_summary.run_id_from_path(out_dir),
            extra={"phases": ",".join(lbl for _, lbl in present)},
        )
    )
    parts.append(
        "Concatenated phase summaries. Each source file remains in this "
        "directory for machine consumption."
    )
    parts.append("")
    for fname, _label in present:
        parts.append("---")
        parts.append("")
        parts.append(f"<!-- source: {fname} -->")
        body = (out_dir / fname).read_text(encoding="utf-8")
        parts.append(body.rstrip())
        parts.append("")
    _summary.write_md(out_dir / "run.summary.md", "\n".join(parts))
    _trace.log("run.summary", phases=[lbl for _, lbl in present])


@click.command()
@click.option("--gaps", "gaps_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--params",
    "params_path",
    required=False,
    default=None,
    type=click.Path(exists=True, path_type=Path),
    help=(
        "JSON file: { '<template-stem>': { param: value, ... }, ... }. "
        "OPTIONAL since v0.9.0 — when omitted, Scaffold derives "
        "parameter defaults from findings.json/run_scope via "
        "prefill_params(). Supplied keys overlay prefilled values at the "
        "per-template-stem level. Engine-owned keys (e.g. "
        "archetype-policies.assignments) are always rebuilt from the "
        "baseline and cannot be overridden — if present in --params they "
        "are stripped with a warning."
    ),
)
@click.option("--out", "out_dir", required=True, type=click.Path(path_type=Path))
@click.option(
    "--rewrite-names/--no-rewrite-names",
    "rewrite_names",
    default=None,
    help=(
        "v0.8.0: rewrite canonical SLZ MG role names to your tenant's names "
        "inside emitted Bicep (requires a non-empty mg_alias.json in the "
        "gaps-file directory). Default (neither flag) AUTO: enable when "
        "mg_alias.json is present AND at least one management-groups "
        "createX flag is false (brownfield with existing MGs). Pass "
        "--no-rewrite-names to force canonical names (cross-tenant reuse); "
        "pass --rewrite-names to force even on empty alias map."
    ),
)
@click.option(
    "--include-placeholders",
    is_flag=True,
    default=False,
    help=(
        "Emit archetype policy assignments whose baseline parameters still "
        "contain ALZ placeholders (all-zero subscription GUIDs, /placeholder/ "
        "segments). Default OFF — such assignments are SKIPPED with a warning, "
        "because emitting them verbatim makes `az deployment ... create` "
        "what-if fail with opaque validation errors. Use this flag only when "
        "you intend to hand-edit the emitted *.parameters.json before deploy."
    ),
)
def main(
    gaps_path: Path,
    params_path: Path | None,
    out_dir: Path,
    rewrite_names: bool | None,
    include_placeholders: bool,
) -> None:
    gaps_doc = json.loads(gaps_path.read_text(encoding="utf-8"))
    gaps = gaps_doc.get("gaps", gaps_doc) if isinstance(gaps_doc, dict) else gaps_doc
    user_params: dict[str, dict[str, Any]] = (
        json.loads(params_path.read_text(encoding="utf-8")) if params_path else {}
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    # v0.7.0: Scaffold reads ``mg_alias.json`` from the gaps file's parent
    # (the canonical artifacts/<run>/ directory). Falls back to out_dir
    # when gaps is supplied from elsewhere.
    run_dir = gaps_path.parent
    # Pull tenant_id + findings from findings.json (the same run_dir) so
    # how-to-deploy.md can pre-fill ``$tenantRootMgId`` AND prefill_params()
    # can derive workspace/region defaults. Silently omit when findings
    # aren't present (back-compat: callers can invoke Scaffold against a
    # hand-crafted gaps.json).
    tenant_id: str | None = None
    run_scope: dict[str, Any] = {}
    findings: list[dict[str, Any]] = []
    findings_path = run_dir / "findings.json"
    if findings_path.exists():
        try:
            fdoc = json.loads(findings_path.read_text(encoding="utf-8"))
            if isinstance(fdoc, dict):
                run_scope = fdoc.get("run_scope") or {}
                if isinstance(run_scope, dict):
                    tid = run_scope.get("tenant_id")
                    if isinstance(tid, str) and tid:
                        tenant_id = tid
                raw_findings = fdoc.get("findings") or []
                if isinstance(raw_findings, list):
                    findings = [f for f in raw_findings if isinstance(f, dict)]
        except (OSError, json.JSONDecodeError):
            tenant_id = None
            run_scope = {}
            findings = []

    # Phase D: derive defaults, strip engine-owned overrides, merge.
    # alias_map (when present) lets prefill_params resolve the *real* parent
    # of the SLZ root for management-groups.parameters (finding H2 — the
    # tenant_id default would re-parent the SLZ root and discard any
    # intermediate MGs the operator already has).
    alias_map = _load_alias_for_doc(run_dir)
    prefilled = prefill_params(findings, gaps, run_scope, alias_map=alias_map)
    cleaned_user, engine_owned_warnings = strip_engine_owned_fields(user_params)
    params_by_template = merge_params(prefilled, cleaned_user)
    key_origin = classify_keys(prefilled, cleaned_user)
    # v0.12.1 — flag policy-critical location keys that neither prefill
    # (no workspace-with-location in findings) nor the operator supplied.
    # The Scaffold prompt reads this list to force a two-field ask_user
    # (primary_location + allowed_locations) before accepting defaults.
    needs_input = needs_operator_input_keys(prefilled, cleaned_user)
    with _trace.tracer(out_dir, phase="scaffold"):
        _trace.log(
            "scaffold.begin",
            gap_count=len(gaps),
            rewrite_names=rewrite_names,
            include_placeholders=include_placeholders,
            prefilled_templates=sorted(prefilled.keys()),
            operator_params_supplied=params_path is not None,
            needs_operator_input_count=len(needs_input),
        )
        # Emit scaffold.params.auto.json so the operator can see the final
        # merged param set + which keys came from prefill vs. their input.
        (out_dir / "scaffold.params.auto.json").write_text(
            json.dumps(
                {
                    "params_by_template": params_by_template,
                    "key_origin": key_origin,
                    "needs_operator_input": needs_input,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            emitted, warnings = scaffold_for_gaps(
                gaps,
                params_by_template,
                out_dir,
                run_dir=run_dir,
                rewrite_names=rewrite_names,
                include_placeholders=include_placeholders,
            )
        except ScaffoldError as exc:
            click.echo(f"SCAFFOLD ERROR: {exc}", err=True)
            sys.exit(2)
        # Prepend engine-owned override warnings (Phase D) so the summary
        # surfaces them alongside engine warnings.
        warnings = list(engine_owned_warnings) + list(warnings)
        # v0.12.1 — surface location-input gap as a WARNING even if the
        # agent bypasses the prompt. Without this, empty
        # listOfAllowedLocations silently denies every region under
        # enforce (and flags every resource under audit).
        if needs_input:
            missing_paths = ", ".join(
                f"{e['template']}.{e['key']}" for e in needs_input
            )
            warnings.insert(
                0,
                "[location-input-required] The following policy-critical "
                f"parameters were not derivable from findings: {missing_paths}. "
                "Prompt the operator for primary_location + allowed_locations "
                "via ask_user (see .github/prompts/slz-scaffold.prompt.md) "
                "before running `az deployment … create`.",
            )
        _trace.log("scaffold.end", emitted_count=len(emitted), warning_count=len(warnings))
        (out_dir / "scaffold.manifest.json").write_text(
            json.dumps({"emitted": emitted, "warnings": warnings}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_scaffold_summary(
            out_dir=out_dir,
            gaps=gaps,
            emitted=emitted,
            warnings=warnings,
            run_dir=run_dir,
        )
        # Resolve the tri-state rewrite_names the same way the engine does
        # so how-to-deploy.md reflects the actual emitted Bicep (the engine
        # does the authoritative decision internally; we mirror it here so
        # the "Brownfield retargeting (applied)" doc block accurately
        # tracks the auto-flip).
        _mg_params = params_by_template.get("management-groups") or {}
        _has_brownfield = any(
            k.startswith("create") and v is False for k, v in _mg_params.items()
        )
        _alias_for_doc = _load_alias_for_doc(run_dir)
        if rewrite_names is None:
            resolved_rewrite_names = bool(_alias_for_doc) and _has_brownfield
        else:
            resolved_rewrite_names = bool(rewrite_names)
        _write_how_to_deploy(
            out_dir=out_dir,
            emitted=emitted,
            run_dir=run_dir,
            rewrite_names=resolved_rewrite_names,
            tenant_id=tenant_id,
        )
        _write_run_rollup(out_dir)
    click.echo(f"Emitted {len(emitted)} templates -> {out_dir}")
    if warnings:
        click.echo(f"  with {len(warnings)} warnings (see scaffold.manifest.json)")
    # Fix 6: hard-fail only if no templates emitted at all. Per-gap skips are
    # surfaced as warnings in scaffold.manifest.json.
    if gaps and not emitted:
        click.echo(
            "SCAFFOLD ERROR: no templates emitted for any of the supplied gaps. "
            "See warnings in scaffold.manifest.json.",
            err=True,
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
