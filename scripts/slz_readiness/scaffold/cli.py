"""`slz-scaffold` — consumes gaps.json + params.json and emits Bicep/params files."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from .. import _summary, _trace
from .engine import ScaffoldError, scaffold_for_gaps
from .template_registry import RULE_TO_TEMPLATE, TEMPLATE_SCOPES

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


def _unscaffolded_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return gaps the scaffold engine refuses to emit Bicep for.

    Two buckets (preserved distinctly in the JSON summary):

    * ``status == "unknown"`` — discovery couldn't verify; cannot scaffold a
      fix we can't verify.
    * no ``RULE_TO_TEMPLATE`` entry — no template covers this rule yet.
    """
    out: list[dict[str, Any]] = []
    for g in gaps:
        rule_id = g.get("rule_id", "")
        status = g.get("status", "missing")
        if status == "unknown":
            out.append({**g, "_reason": "unknown"})
            continue
        if rule_id not in RULE_TO_TEMPLATE:
            out.append({**g, "_reason": "no_template"})
    out.sort(key=lambda g: (g.get("rule_id", ""), g.get("resource_id", "")))
    return out


def _deploy_commands(emitted: list[dict[str, Any]]) -> dict[str, list[str]]:
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
    """
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
    bash_lines: list[str] = ['MG_ID="<your-mg-id>"', 'LOCATION="<your-region>"']
    pwsh_lines: list[str] = ['$mgId = "<your-mg-id>"', '$location = "<your-region>"']
    if needs_rg:
        bash_lines.append('RG_NAME="<your-resource-group>"')
        pwsh_lines.append('$rgName = "<your-resource-group>"')
    bash_lines.append("")
    pwsh_lines.append("")
    for e in by_order:
        template = e.get("template", "")
        bicep = e.get("bicep", "")
        params = e.get("params", "")
        phase = e.get("rollout_phase")
        phase_hint = f" (rolloutPhase={phase})" if phase else ""
        scope = TEMPLATE_SCOPES.get(template, "managementGroup")

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
            bash_head = (
                'az deployment mg {verb} --management-group-id "$MG_ID" '
                '--location "$LOCATION"'
            )
            pwsh_head = (
                "az deployment mg {verb} --management-group-id $mgId "
                "--location $location"
            )

        bash_lines.append(f"# {template}{phase_hint} — what-if first, then create")
        for verb in ("what-if", "create"):
            bash_lines.append(
                f"{bash_head.format(verb=verb)} \\\n"
                f"    --template-file {bicep} \\\n"
                f"    --parameters @{params}"
            )
        bash_lines.append("")

        pwsh_lines.append(f"# {template}{phase_hint} — what-if first, then create")
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
) -> None:
    """Emit ``how-to-deploy.md`` with Wave-1/Wave-2 recipes in Bash + PowerShell.

    Required by operating instructions §5. The scaffold phase never runs the
    deployment — this file is the HITL hand-off to the human operator.
    """
    if not emitted:
        return
    cmds = _deploy_commands(emitted)
    has_dine = any(e.get("template") in {"archetype-policies"} for e in emitted)
    has_phased = any(e.get("rollout_phase") for e in emitted)
    needs_rg = any(
        TEMPLATE_SCOPES.get(e.get("template", ""), "managementGroup") == "resourceGroup"
        for e in emitted
    )
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
    alias_map = _load_alias_for_doc(run_dir)
    if alias_map:
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
    parts.append(
        "1. **`management-groups`** — creates the MG tree each subsequent "
        "assignment references. Skip if your MG hierarchy already matches "
        "`data/baseline/alz-library`.\n"
        "2. **`log-analytics`** (subscription scope) — creates the "
        "`rg-slz-management` resource group *and* the workspace. Deploy "
        "before any policy that references the workspace id.\n"
        "3. **`archetype-policies`** / **`sovereignty-*-policies`** (MG scope) — "
        "policy assignments. Deploy *after* management-groups and "
        "log-analytics, otherwise the `policyDefinitionId` / "
        "`workspaceResourceId` references will fail validation."
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
) -> None:
    unscaffolded = _unscaffolded_gaps(gaps)
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
            "These gaps did not produce Bicep output. `unknown` gaps require "
            "elevated discovery; `no_template` gaps need a new entry in "
            "`scripts/slz_readiness/scaffold/template_registry.py`."
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
        cmds = _deploy_commands(emitted)
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
    sections = [
        ("discover.summary.md", "Discover"),
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
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="JSON file: { '<template-stem>': { param: value, ... }, ... }",
)
@click.option("--out", "out_dir", required=True, type=click.Path(path_type=Path))
def main(gaps_path: Path, params_path: Path, out_dir: Path) -> None:
    gaps_doc = json.loads(gaps_path.read_text(encoding="utf-8"))
    gaps = gaps_doc.get("gaps", gaps_doc) if isinstance(gaps_doc, dict) else gaps_doc
    params_by_template = json.loads(params_path.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    # v0.7.0: Scaffold reads ``mg_alias.json`` from the gaps file's parent
    # (the canonical artifacts/<run>/ directory). Falls back to out_dir
    # when gaps is supplied from elsewhere.
    run_dir = gaps_path.parent
    with _trace.tracer(out_dir, phase="scaffold"):
        _trace.log("scaffold.begin", gap_count=len(gaps))
        try:
            emitted, warnings = scaffold_for_gaps(
                gaps, params_by_template, out_dir, run_dir=run_dir
            )
        except ScaffoldError as exc:
            click.echo(f"SCAFFOLD ERROR: {exc}", err=True)
            sys.exit(2)
        _trace.log("scaffold.end", emitted_count=len(emitted), warning_count=len(warnings))
        (out_dir / "scaffold.manifest.json").write_text(
            json.dumps({"emitted": emitted, "warnings": warnings}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_scaffold_summary(out_dir=out_dir, gaps=gaps, emitted=emitted, warnings=warnings)
        _write_how_to_deploy(out_dir=out_dir, emitted=emitted, run_dir=run_dir)
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
