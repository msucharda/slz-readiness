"""Golden-ish tests for the v0.14.0 opt-in deploy orchestrator emit.

These tests exercise ``scripts.slz_readiness.scaffold.deploy_script`` in
isolation: they feed a synthetic ``emitted`` manifest + optional alias
map and assert the rendered shell/pwsh scripts are well-formed.

They deliberately *do not* snapshot the entire rendered file — the
exact wording of echo strings and header comments is allowed to drift
with the recipe in ``how-to-deploy.md``. Each test pins one invariant
the module promises callers.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from slz_readiness.scaffold.deploy_script import (
    _DEPLOY_ORDER,
    _plan_steps,
    write_deploy_script,
)


def _mk_emitted(*templates: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for t in templates:
        out.append(
            {
                "template": t,
                "scope": "corp" if "archetype" in t else "",
                "bicep": f"bicep/{t}.bicep",
                "params": f"bicep/{t}.parameters.json",
                "rollout_phase": "audit",
            }
        )
    return out


def test_returns_empty_on_no_emit(tmp_path: Path) -> None:
    """Empty ``emitted`` → no files written, empty list returned."""
    assert write_deploy_script(out_dir=tmp_path, emitted=[]) == []
    assert not (tmp_path / "runbooks").exists()


def test_emits_both_shells(tmp_path: Path) -> None:
    """Every run emits both the bash and pwsh orchestrators."""
    emitted = _mk_emitted("management-groups", "log-analytics")
    written = write_deploy_script(out_dir=tmp_path, emitted=emitted)
    assert "runbooks/deploy-all.sh" in written
    assert "runbooks/deploy-all.ps1" in written
    assert (tmp_path / "runbooks" / "deploy-all.sh").is_file()
    assert (tmp_path / "runbooks" / "deploy-all.ps1").is_file()


def test_what_if_precedes_create(tmp_path: Path) -> None:
    """In --apply mode, each template's what-if appears before its create."""
    emitted = _mk_emitted("management-groups", "log-analytics", "archetype-policies")
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    # The --apply branch is a per-step deploy block where what-if precedes create.
    # The APPLY gate separates the two modes.
    assert 'if [[ "$APPLY" == "true" ]]' in sh
    # In the --apply branch, each template's what-if comes before its create.
    apply_idx = sh.index('if [[ "$APPLY" == "true" ]]')
    apply_block = sh[apply_idx:]
    for tmpl in ("management-groups", "log-analytics", "archetype-policies"):
        tmpl_start = apply_block.index(f"{tmpl}")
        after_tmpl = apply_block[tmpl_start:]
        assert "what-if" in after_tmpl[:200]


def test_canonical_deploy_order_preserved(tmp_path: Path) -> None:
    """Templates appear in canonical ``_DEPLOY_ORDER`` — not manifest order."""
    # Feed in reverse order; the renderer must re-sort.
    emitted = _mk_emitted("archetype-policies", "log-analytics", "management-groups")
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    mg_at = sh.index("management-groups")
    la_at = sh.index("log-analytics")
    ap_at = sh.index("archetype-policies")
    # Canonical order: management-groups < log-analytics < archetype-policies
    assert mg_at < la_at < ap_at


def test_minimal_profile_emits_subset(tmp_path: Path) -> None:
    """--scaffold-profile minimal emits fewer templates → orchestrator skips them.

    Regression: before v0.14.0, any deploy-all script would always include
    every canonical step and fail at runtime when alz-policy-definitions
    wasn't actually on disk. The renderer must iterate the manifest, not
    ``_DEPLOY_ORDER`` directly.
    """
    emitted = _mk_emitted(
        "management-groups",
        "sovereignty-global-policies",
        "sovereignty-confidential-policies",
    )
    write_deploy_script(
        out_dir=tmp_path,
        emitted=emitted,
        alias_map={"slz": "contoso-slz"},
    )
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    assert "archetype-policies" not in sh
    assert "alz-policy-definitions" not in sh


def test_brownfield_gate_requires_override(tmp_path: Path) -> None:
    """Non-empty alias_map → script body contains a gate requiring --skip-mg-prereq."""
    emitted = _mk_emitted("management-groups")
    write_deploy_script(
        out_dir=tmp_path,
        emitted=emitted,
        alias_map={"slz": "contoso-slz", "corp": "contoso-corp"},
    )
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    assert "SKIP_MG_PREREQ" in sh
    assert "--skip-mg-prereq" in sh
    assert "corp -> contoso-corp" in sh
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")
    assert "SkipMgPrereq" in ps1
    assert "corp -> contoso-corp" in ps1


def test_greenfield_omits_brownfield_gate(tmp_path: Path) -> None:
    """Empty alias_map → no brownfield gate block emitted."""
    emitted = _mk_emitted("management-groups")
    write_deploy_script(out_dir=tmp_path, emitted=emitted, alias_map={})
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    assert "Brownfield alias map" not in sh


def test_dine_grant_script_emitted_only_with_archetype(tmp_path: Path) -> None:
    """``grant-dine-roles.*`` emitted only when archetype-policies is in emit set."""
    emitted = _mk_emitted("management-groups", "log-analytics")
    written = write_deploy_script(out_dir=tmp_path, emitted=emitted)
    assert all("grant-dine-roles" not in p for p in written)

    emitted_with_dine = _mk_emitted(
        "management-groups", "log-analytics", "archetype-policies"
    )
    out2 = tmp_path / "run2"
    out2.mkdir()
    written2 = write_deploy_script(out_dir=out2, emitted=emitted_with_dine)
    assert "runbooks/grant-dine-roles.sh" in written2
    assert "runbooks/grant-dine-roles.ps1" in written2


def test_sovereignty_global_uses_slz_root_var(tmp_path: Path) -> None:
    """sovereignty-global-policies step targets $SLZ_ROOT_MG_ID / $slzRootMgId,
    not the generic $MG_ID variable."""
    emitted = _mk_emitted("management-groups", "sovereignty-global-policies")
    write_deploy_script(
        out_dir=tmp_path,
        emitted=emitted,
        alias_map={"slz": "contoso-slz"},
    )
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    # The sovereignty-global-policies block must reference $SLZ_ROOT_MG_ID.
    idx = sh.index("sovereignty-global-policies")
    nearby = sh[idx : idx + 600]
    assert "$SLZ_ROOT_MG_ID" in nearby
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")
    idx_ps = ps1.index("sovereignty-global-policies")
    assert "$slzRootMgId" in ps1[idx_ps : idx_ps + 600]


def test_scope_verb_matches_scope(tmp_path: Path) -> None:
    """``az deployment <verb>`` matches ``TEMPLATE_SCOPES`` entries."""
    emitted = _mk_emitted(
        "management-groups",  # managementGroup -> mg
        "log-analytics",  # managementGroup -> mg (policy), or mg by default
    )
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    # All emitted templates use managementGroup scope → only `az deployment mg` verbs.
    assert "az deployment mg what-if" in sh
    assert "az deployment mg create" in sh


def test_plan_steps_matches_deploy_order() -> None:
    """``_plan_steps`` re-sorts manifest entries by _DEPLOY_ORDER index."""
    emitted = _mk_emitted("archetype-policies", "management-groups", "log-analytics")
    steps = _plan_steps(emitted)
    assert [s.template for s in steps] == [
        "management-groups",
        "log-analytics",
        "archetype-policies",
    ]


def test_deploy_order_matches_cli() -> None:
    """Module-local ``_DEPLOY_ORDER`` must stay in lockstep with cli._DEPLOY_ORDER.

    If this diverges, either the cli ordering changed (update deploy_script)
    or vice-versa — both callers must agree on canonical order.
    """
    from slz_readiness.scaffold import cli as scaffold_cli
    assert _DEPLOY_ORDER == scaffold_cli._DEPLOY_ORDER


def test_mg_variable_resolution_parity_with_cli() -> None:
    """_plan_steps MG variables must match cli._deploy_commands output.

    Both modules duplicate a 5-branch if/elif for MG variable resolution
    (sovereignty-global / alz-policy-def -> $SLZ_ROOT_MG_ID,
    sovereignty-confidential -> per-scope alias,
    archetype-policies -> per-scope,
    management-groups -> $TENANT_ROOT_MG_ID,
    everything else -> $MG_ID). If one side drifts from the other,
    how-to-deploy.md (cli) will document a different MG target than
    deploy-all.{sh,ps1} (deploy_script) actually deploys to. Silent
    wrong-target deployments.
    """
    from slz_readiness.scaffold import cli as scaffold_cli

    emitted = [
        {
            "template": "management-groups",
            "scope": "tenant",
            "bicep": "bicep/management-groups.bicep",
            "params": "params/management-groups.parameters.json",
            "rule_ids": [],
            "rollout_phase": "audit",
        },
        {
            "template": "alz-policy-definitions",
            "scope": "tenant",
            "bicep": "bicep/alz-policy-definitions.bicep",
            "params": "params/alz-policy-definitions.parameters.json",
            "rule_ids": [],
            "rollout_phase": "audit",
        },
        {
            "template": "sovereignty-global-policies",
            "scope": "tenant",
            "bicep": "bicep/sovereignty-global-policies.bicep",
            "params": "params/sovereignty-global-policies.parameters.json",
            "rule_ids": [],
            "rollout_phase": "audit",
        },
        {
            "template": "sovereignty-confidential-policies",
            "scope": "confidential_corp",
            "bicep": "bicep/sovereignty-confidential-policies.bicep",
            "params": "params/sovereignty-confidential-policies.parameters.json",
            "rule_ids": [],
            "rollout_phase": "audit",
        },
        {
            "template": "archetype-policies",
            "scope": "corp",
            "bicep": "bicep/archetype-policies.bicep",
            "params": "params/archetype-policies.parameters.json",
            "rule_ids": [],
            "rollout_phase": "audit",
        },
        {
            "template": "policy-assignment",
            "scope": "corp",
            "bicep": "bicep/policy-assignment.bicep",
            "params": "params/policy-assignment.parameters.json",
            "rule_ids": [],
            "rollout_phase": "audit",
        },
        {
            "template": "role-assignment",
            "scope": "online",
            "bicep": "bicep/role-assignment.bicep",
            "params": "params/role-assignment.parameters.json",
            "rule_ids": [],
            "rollout_phase": "audit",
        },
    ]
    alias_map = {
        "slz": "alz",
        "confidential_corp": "conf-corp",
        "corp": "corp",
        "online": "online",
    }

    steps = _plan_steps(emitted, alias_map=alias_map)
    cmds = scaffold_cli._deploy_commands(emitted, alias_map=alias_map)

    # For every step rendered by _plan_steps, find the corresponding
    # create/what-if line emitted by _deploy_commands and assert the MG
    # target token matches.
    expected_bash: dict[str, str] = {
        "management-groups": "$TENANT_ROOT_MG_ID",
        "alz-policy-definitions": "$SLZ_ROOT_MG_ID",
        "sovereignty-global-policies": "$SLZ_ROOT_MG_ID",
        "sovereignty-confidential-policies": '"conf-corp"',
        "archetype-policies": '"corp"',
        "policy-assignment": '"corp"',
        "role-assignment": '"online"',
    }
    for step in steps:
        token = expected_bash[step.template]
        assert step.mg_bash_var == token, (step.template, step.mg_bash_var, token)
        matching_lines = [
            line for line in cmds["bash"]
            if step.template in line and ("what-if" in line or "create" in line)
        ]
        assert matching_lines, (step.template, cmds["bash"])
        assert any(token in line for line in matching_lines), (
            step.template,
            token,
            matching_lines,
        )


def test_needs_generic_mg_parity_with_cli() -> None:
    """``needs_generic_mg`` must agree between deploy_script and cli.

    The runbook renderer (``deploy_script.write_deploy_script``) derives
    ``needs_generic_mg`` from ``steps[*].mg_pwsh_var is None``; the
    how-to-deploy renderer (``cli._needs_generic_mg``) duplicates the
    per-template resolution table. If one drifts from the other,
    ``how-to-deploy.md`` can ask operators to populate ``$mgId`` that
    the runbook never references (or omit it when the runbook does).
    This locks the equivalence for both the "needed" and "not needed"
    branches.
    """
    from slz_readiness.scaffold import cli as scaffold_cli

    def _cli_side(emitted: list[dict[str, Any]], alias_map: dict[str, str]) -> bool:
        by_order = sorted(
            emitted,
            key=lambda e: (
                scaffold_cli._DEPLOY_ORDER.index(e["template"])
                if e["template"] in scaffold_cli._DEPLOY_ORDER
                else 99,
                e.get("scope", ""),
            ),
        )
        return scaffold_cli._needs_generic_mg(by_order, alias_map)

    def _deploy_side(emitted: list[dict[str, Any]], alias_map: dict[str, str]) -> bool:
        steps = _plan_steps(emitted, alias_map=alias_map)
        return any(
            s.scope == "managementGroup" and s.mg_pwsh_var is None for s in steps
        )

    # Case 1: only templates that bind to a dedicated MG var -> no generic MG.
    no_generic = [
        {"template": "management-groups", "scope": "tenant",
         "bicep": "", "params": "", "rule_ids": []},
        {"template": "alz-policy-definitions", "scope": "tenant",
         "bicep": "", "params": "", "rule_ids": []},
        {"template": "sovereignty-global-policies", "scope": "tenant",
         "bicep": "", "params": "", "rule_ids": []},
        {"template": "archetype-policies", "scope": "corp",
         "bicep": "", "params": "", "rule_ids": []},
        {"template": "sovereignty-confidential-policies", "scope": "confidential_corp",
         "bicep": "", "params": "", "rule_ids": []},
    ]
    alias = {"slz": "alz", "confidential_corp": "conf-corp"}
    assert _cli_side(no_generic, alias) is False
    assert _deploy_side(no_generic, alias) is False
    assert _cli_side(no_generic, alias) == _deploy_side(no_generic, alias)

    # Case 2: policy-assignment with a concrete scope inlines its target
    # archetype MG -> no generic MG needed.
    no_generic_assignment = [
        {"template": "management-groups", "scope": "tenant",
         "bicep": "", "params": "", "rule_ids": []},
        {"template": "policy-assignment", "scope": "corp",
         "bicep": "", "params": "", "rule_ids": []},
        {"template": "role-assignment", "scope": "online",
         "bicep": "", "params": "", "rule_ids": []},
    ]
    assert _cli_side(no_generic_assignment, alias) is False
    assert _deploy_side(no_generic_assignment, alias) is False

    # Case 2b: policy-assignment with an empty scope (the rare
    # scope-less fallback) still needs the generic ``$MG_ID`` — the
    # scaffold engine couldn't bind it to a concrete archetype.
    with_generic = [
        {"template": "management-groups", "scope": "tenant",
         "bicep": "", "params": "", "rule_ids": []},
        {"template": "policy-assignment", "scope": "",
         "bicep": "", "params": "", "rule_ids": []},
    ]
    assert _cli_side(with_generic, alias) is True
    assert _deploy_side(with_generic, alias) is True

    # Case 3: confidential-policies with scope_name but no alias → uses
    # canonical scope name directly (no generic MG needed).
    conf_no_alias = [
        {"template": "sovereignty-confidential-policies", "scope": "confidential_corp",
         "bicep": "", "params": "", "rule_ids": []},
    ]
    assert _cli_side(conf_no_alias, {}) is False
    assert _deploy_side(conf_no_alias, {}) is False

    # Case 3b: confidential-policies with EMPTY scope (rare) → needs generic MG.
    conf_empty_scope = [
        {"template": "sovereignty-confidential-policies", "scope": "",
         "bicep": "", "params": "", "rule_ids": []},
    ]
    assert _cli_side(conf_empty_scope, {}) is True
    assert _deploy_side(conf_empty_scope, {}) is True


def test_how_to_deploy_prerequisites_omit_unused_mg_id(tmp_path: Path) -> None:
    """``how-to-deploy.md`` prerequisites block mirrors the runbook.

    Regression companion to ``test_generic_mg_id_omitted_when_unused``:
    when no emitted step references ``$mgId`` / ``MG_ID``, the
    prerequisites variable listing in ``how-to-deploy.md`` must not
    declare them either. Otherwise the doc instructs operators to fill
    a variable the runbook never uses — and, worse, ``TENANT_ROOT_MG_ID``
    stayed as a placeholder despite a known tenant id.
    """
    from slz_readiness.scaffold import cli as scaffold_cli

    emitted = _mk_emitted(
        "management-groups",
        "archetype-policies",
        "alz-policy-definitions",
        "sovereignty-global-policies",
    )
    tenant = "11111111-2222-3333-4444-555555555555"
    scaffold_cli._write_how_to_deploy(
        out_dir=tmp_path,
        emitted=emitted,
        tenant_id=tenant,
    )
    body = (tmp_path / "how-to-deploy.md").read_text(encoding="utf-8")
    # Unused generic MG vars must not appear in either code fence.
    assert '$mgId = "<your-mg-id>"' not in body
    assert 'MG_ID="<your-mg-id>"' not in body
    # Tenant-root default is inlined when tenant_id is supplied.
    assert f'$tenantRootMgId = "{tenant}"' in body
    assert f'TENANT_ROOT_MG_ID="{tenant}"' in body


def test_runbook_filenames_on_allowlist() -> None:
    """Emitted runbook filenames must appear in ``ALLOWED_RUNBOOKS``.

    Guards the contract that post_tool_use hooks enforce when validating
    scaffold.manifest.json.
    """
    from slz_readiness.scaffold.template_registry import ALLOWED_RUNBOOKS
    for name in ("deploy-all.sh", "deploy-all.ps1", "grant-dine-roles.sh", "grant-dine-roles.ps1"):
        assert name in ALLOWED_RUNBOOKS


@pytest.mark.parametrize("shell", ["sh", "ps1"])
def test_script_declares_fail_fast(tmp_path: Path, shell: str) -> None:
    """Both shells must declare fail-fast semantics at the top."""
    emitted = _mk_emitted("management-groups")
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    body = (tmp_path / "runbooks" / f"deploy-all.{shell}").read_text(encoding="utf-8")
    if shell == "sh":
        assert "set -euo pipefail" in body
    else:
        assert "$ErrorActionPreference = 'Stop'" in body


# ---------------------------------------------------------------------------
# Regression tests for slz-demo runbook defects (see research report
# ``take-a-look-at-slz-demo-runbook-scripts-generated-.md``).
# ---------------------------------------------------------------------------


def _pwsh_available() -> bool:
    return shutil.which("pwsh") is not None or shutil.which("powershell") is not None


def _pwsh_bin() -> str:
    return shutil.which("pwsh") or shutil.which("powershell") or "pwsh"


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="bash -n regression-check runs on POSIX (Linux/macOS CI) only",
)
def test_sh_parses_with_bash_n(tmp_path: Path) -> None:
    """Emitted ``deploy-all.sh`` must be syntactically valid bash.

    Regression: the ``{dine_footer}`` interpolation inside the outer
    ``echo "..."`` produced an unterminated single-quote literal whenever
    archetype-policies was present.
    """
    emitted = _mk_emitted(
        "management-groups",
        "log-analytics",
        "archetype-policies",
    )
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    sh_path = tmp_path / "runbooks" / "deploy-all.sh"
    proc = subprocess.run(
        ["bash", "-n", str(sh_path)], capture_output=True, text=True
    )
    assert proc.returncode == 0, f"bash -n failed: {proc.stderr}"


@pytest.mark.skipif(not _pwsh_available(), reason="pwsh/powershell not available")
def test_ps1_parses_with_parser(tmp_path: Path) -> None:
    """Emitted ``deploy-all.ps1`` must parse cleanly.

    Regression: the ``{dine_footer}`` interpolation inside the outer
    ``Write-Host "..."`` produced an unterminated string literal whenever
    archetype-policies was present.
    """
    emitted = _mk_emitted(
        "management-groups",
        "log-analytics",
        "archetype-policies",
    )
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    ps1_path = tmp_path / "runbooks" / "deploy-all.ps1"
    # Use the PowerShell language parser to check syntax without executing.
    script = (
        "$tokens = $null; $errs = $null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{ps1_path.as_posix()}', "
        "[ref]$tokens, [ref]$errs) | Out-Null; "
        "if ($errs -and $errs.Count -gt 0) { "
        "  $errs | ForEach-Object { Write-Error $_.Message }; exit 1 "
        "}; exit 0"
    )
    proc = subprocess.run(
        [_pwsh_bin(), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"pwsh parse failed: {proc.stderr}"


def test_scripts_self_locate_to_run_root(tmp_path: Path) -> None:
    """Both scripts re-anchor to the run-root so relative bicep/ paths resolve."""
    emitted = _mk_emitted("management-groups")
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")
    assert 'cd -- "$(dirname -- "$0")/.."' in sh
    assert "$PSScriptRoot" in ps1
    assert "Set-Location" in ps1


def test_archetype_policies_uses_per_scope_mg_id(tmp_path: Path) -> None:
    """Each archetype-policies emission targets its own MG id, not ``$MG_ID``."""
    emitted: list[dict[str, object]] = [
        {
            "template": "archetype-policies",
            "scope": "corp",
            "bicep": "bicep/archetype-policies-corp.bicep",
            "params": "bicep/archetype-policies-corp.parameters.json",
            "rollout_phase": "audit",
        },
        {
            "template": "archetype-policies",
            "scope": "identity",
            "bicep": "bicep/archetype-policies-identity.bicep",
            "params": "bicep/archetype-policies-identity.parameters.json",
            "rollout_phase": "audit",
        },
    ]
    write_deploy_script(
        out_dir=tmp_path,
        emitted=emitted,
        alias_map={"identity": "contoso-identity"},
    )
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    # corp falls back to the canonical scope name (no alias)
    assert '--management-group-id "corp"' in sh
    # identity resolves via the alias map
    assert '--management-group-id "contoso-identity"' in sh
    # archetype-policies must NOT collapse onto the generic placeholder
    corp_idx = sh.index("archetype-policies-corp")
    corp_block = sh[corp_idx : corp_idx + 600]
    assert '"$MG_ID"' not in corp_block


def test_management_groups_uses_tenant_root_var(tmp_path: Path) -> None:
    """management-groups step targets ``$TENANT_ROOT_MG_ID`` / ``$tenantRootMgId``,
    not the generic archetype ``$MG_ID``."""
    emitted = _mk_emitted("management-groups")
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")

    assert 'TENANT_ROOT_MG_ID="<your-tenant-root-mg-id>"' in sh
    assert '$tenantRootMgId = "<your-tenant-root-mg-id>"' in ps1

    # The management-groups step must use the tenant-root variable.
    # Skip header comment hits — search for the actual deploy step echo line.
    sh_mg_idx = sh.index("--> [1/1] management-groups")
    sh_mg_block = sh[sh_mg_idx : sh_mg_idx + 800]
    assert "$TENANT_ROOT_MG_ID" in sh_mg_block
    # ...and not the plain $MG_ID
    assert "--management-group-id \"$MG_ID\"" not in sh_mg_block

    ps1_mg_idx = ps1.index("--> [1/1] management-groups")
    ps1_mg_block = ps1[ps1_mg_idx : ps1_mg_idx + 800]
    assert "$tenantRootMgId" in ps1_mg_block


def test_tenant_id_inlined_when_provided(tmp_path: Path) -> None:
    """When Discover supplies ``--tenant``, inline it as the TENANT_EXPECTED
    default and as the default for TENANT_ROOT_MG_ID.

    Rationale: tenant ids are not secrets (they appear in every Azure
    Portal URL, in JWT ``tid`` claims, and in ``az account show``
    output). Leaving ``TENANT_EXPECTED`` / ``$tenantExpected`` as
    ``<tenant-id>`` meant operators had to hand-edit the Variables
    block on every run, and forgetting to do so was the #1 failure
    mode of the runbook ("mgId still holds the placeholder..."). The
    tenant-root MG id equals the tenant id for the vast majority of
    tenants, so defaulting ``TENANT_ROOT_MG_ID`` to it as well removes
    a redundant edit.
    """
    emitted = _mk_emitted("management-groups")
    tenant = "99554ba8-f985-4a2d-be21-fc3a62570dd4"
    write_deploy_script(
        out_dir=tmp_path,
        emitted=emitted,
        tenant_id=tenant,
    )
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")
    assert f'TENANT_EXPECTED="{tenant}"' in sh
    assert f'$tenantExpected = "{tenant}"' in ps1
    assert f'TENANT_ROOT_MG_ID="{tenant}"' in sh
    assert f'$tenantRootMgId = "{tenant}"' in ps1
    # The placeholder guard must NOT fire on TENANT_ROOT_MG_ID when it
    # was inlined with a concrete tenant id.
    assert "TENANT_ROOT_MG_ID still holds the placeholder" not in sh
    assert "tenantRootMgId still holds the placeholder" not in ps1


def test_tenant_id_omitted_falls_back_to_placeholder(tmp_path: Path) -> None:
    """Without ``tenant_id``, the pre-v0.14.x placeholder behaviour is kept."""
    emitted = _mk_emitted("management-groups")
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")
    assert 'TENANT_EXPECTED="<tenant-id>"' in sh
    assert '$tenantExpected = "<tenant-id>"' in ps1
    assert 'TENANT_ROOT_MG_ID="<your-tenant-root-mg-id>"' in sh
    assert '$tenantRootMgId = "<your-tenant-root-mg-id>"' in ps1


def test_placeholder_guard_blocks_unedited_vars(tmp_path: Path) -> None:
    """Unedited ``<your-*>`` placeholders must fail-fast before any ``az`` call.

    Regression: Windows ``cmd.exe`` (which wraps ``az.cmd``) interprets ``<``
    in an argument as input redirection, masking a 12-step catastrophic
    failure as phantom success ("The system cannot find the file specified.").
    The script must refuse to proceed while any declared variable still holds
    its ``<...>`` placeholder.

    Uses ``policy-assignment`` to force emission of the generic ``MG_ID``
    variable (management-groups + archetype-policies alone resolve every
    step to a template-specific MG variable, so ``MG_ID`` would not be
    declared and its guard would legitimately be absent — see
    ``test_generic_mg_id_omitted_when_unused``).
    """
    emitted = _mk_emitted("management-groups", "archetype-policies", "policy-assignment")
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")

    # Bash: guard uses a glob pattern match against ``<*>``.
    for var in ("MG_ID", "LOCATION", "TENANT_ROOT_MG_ID"):
        assert ('if [[ "${' + var + '}" == "<"*">" ]]') in sh, var
        assert (var + " still holds the placeholder") in sh, var
    # Bash: guard must run BEFORE the first what-if step.
    guard_idx = sh.index("still holds the placeholder")
    whatif_idx = sh.index("Wave 1 what-if pass")
    assert guard_idx < whatif_idx

    # PowerShell: uses ``-like "<*>"`` pattern.
    for var in ("mgId", "location", "tenantRootMgId"):
        assert ('if ($' + var + ' -like "<*>")') in ps1, var
        assert (var + " still holds the placeholder") in ps1, var
    ps1_guard_idx = ps1.index("still holds the placeholder")
    ps1_whatif_idx = ps1.index("Wave 1 what-if pass")
    assert ps1_guard_idx < ps1_whatif_idx


def test_generic_mg_id_omitted_when_unused(tmp_path: Path) -> None:
    """Generic ``MG_ID`` var + guard are suppressed when no step references it.

    Regression (slz-demo 20260420T195152Z): ``deploy-all.ps1`` aborted
    with "mgId still holds the placeholder '<your-mg-id>'" on a run
    whose steps (management-groups, archetype-policies, alz-policy-
    definitions, sovereignty-*) all resolved to template-specific MG
    variables — the ``$mgId`` variable was declared but never used,
    yet the placeholder guard still tripped.
    """
    emitted = _mk_emitted(
        "management-groups",
        "archetype-policies",
        "alz-policy-definitions",
        "sovereignty-global-policies",
    )
    write_deploy_script(
        out_dir=tmp_path,
        emitted=emitted,
        alias_map={"slz": "contoso-slz"},
    )
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")
    assert 'MG_ID="<your-mg-id>"' not in sh
    assert '$mgId = "<your-mg-id>"' not in ps1
    assert '"MG_ID still holds the placeholder' not in sh
    assert '"mgId still holds the placeholder' not in ps1


def test_params_by_template_fills_location_and_roots(tmp_path: Path) -> None:
    """Full prefill → zero ``<...>`` placeholders + zero placeholder_check lines.

    When the scaffold phase has derived a concrete ``listOfAllowedLocations``
    (modal region) + ``parentManagementGroupId`` + ``slz`` alias, the
    emitted runbook must inline those values directly. Operators no longer
    need to edit the Variables block before running ``--apply``; ``what-if``
    is the review gate (see deploy_script.py module docstring).
    """
    emitted = _mk_emitted(
        "management-groups",
        "alz-policy-definitions",
        "sovereignty-global-policies",
        "archetype-policies",
    )
    params_by_template = {
        "sovereignty-global-policies": {
            "listOfAllowedLocations": ["swedencentral", "westeurope"],
        },
        "management-groups": {"parentManagementGroupId": "contoso-root"},
    }
    write_deploy_script(
        out_dir=tmp_path,
        emitted=emitted,
        alias_map={"slz": "contoso-alz"},
        tenant_id="99554ba8-f985-4a2d-be21-fc3a62570dd4",
        params_by_template=params_by_template,
    )
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")

    # Variables block pre-filled from scaffold params.
    assert 'LOCATION="swedencentral"' in sh
    assert '$location = "swedencentral"' in ps1
    assert 'SLZ_ROOT_MG_ID="contoso-alz"' in sh
    assert '$slzRootMgId = "contoso-alz"' in ps1
    assert 'TENANT_ROOT_MG_ID="contoso-root"' in sh
    assert '$tenantRootMgId = "contoso-root"' in ps1

    # No residual angle-bracket placeholders in the Variables block.
    assert '"<your-region>"' not in sh
    assert '"<your-slz-root-mg-id>"' not in sh
    assert '"<your-tenant-root-mg-id>"' not in sh

    # Placeholder guard is only emitted for vars that stayed as ``<...>``;
    # a fully-derived run emits no guard lines at all.
    assert "still holds the placeholder" not in sh
    assert "still holds the placeholder" not in ps1


def test_confidential_null_alias_uses_canonical_name(tmp_path: Path) -> None:
    """sovereignty-confidential-policies with null aliases uses canonical scope name.

    Regression (slz-demo 20260420T195152Z): ``mg_alias.json`` had
    ``"confidential_corp": null, "confidential_online": null``.
    ``load_alias_map()`` filters null entries, so the alias_map was
    ``{"slz": "alz", ...}``. The old code checked
    ``scope_name in alias_map`` — since ``confidential_corp`` was NOT in
    the alias_map, the step fell through to the generic ``$MG_ID``
    placeholder. The fix resolves via ``alias_map.get(scope_name) or
    scope_name``, matching the archetype-policies pattern.
    """
    emitted: list[dict[str, object]] = [
        {
            "template": "management-groups",
            "scope": "tenant",
            "bicep": "bicep/management-groups.bicep",
            "params": "params/management-groups.parameters.json",
            "rollout_phase": "audit",
        },
        {
            "template": "sovereignty-confidential-policies",
            "scope": "confidential_corp",
            "bicep": "bicep/sovereignty-confidential-policies-confidential_corp.bicep",
            "params": "params/sovereignty-confidential-policies-confidential_corp.parameters.json",
            "rollout_phase": "audit",
        },
        {
            "template": "sovereignty-confidential-policies",
            "scope": "confidential_online",
            "bicep": "bicep/sovereignty-confidential-policies-confidential_online.bicep",
            "params": (
                "params/sovereignty-confidential-policies"
                "-confidential_online.parameters.json"
            ),
            "rollout_phase": "audit",
        },
    ]
    # Brownfield: slz aliased, but confidential_* entries are null (filtered
    # out by load_alias_map). This was the exact slz-demo scenario.
    write_deploy_script(
        out_dir=tmp_path,
        emitted=emitted,
        alias_map={"slz": "alz", "landingzones": "workloads", "platform": "platform"},
        tenant_id="99554ba8-f985-4a2d-be21-fc3a62570dd4",
    )
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")

    # Generic $MG_ID must NOT appear — both confidential steps should use
    # their canonical scope name directly.
    assert 'MG_ID="<your-mg-id>"' not in sh
    assert '$mgId = "<your-mg-id>"' not in ps1

    # Each confidential step should use its canonical name inline.
    assert '--management-group-id "confidential_corp"' in sh
    assert '--management-group-id "confidential_online"' in sh
    assert '--management-group-id "confidential_corp"' in ps1
    assert '--management-group-id "confidential_online"' in ps1


def test_confidential_with_alias_uses_alias(tmp_path: Path) -> None:
    """sovereignty-confidential-policies with alias uses the alias value."""
    emitted: list[dict[str, object]] = [
        {
            "template": "sovereignty-confidential-policies",
            "scope": "confidential_corp",
            "bicep": "bicep/sovereignty-confidential-policies-confidential_corp.bicep",
            "params": "params/sovereignty-confidential-policies-confidential_corp.parameters.json",
            "rollout_phase": "audit",
        },
    ]
    write_deploy_script(
        out_dir=tmp_path,
        emitted=emitted,
        alias_map={"confidential_corp": "contoso-conf-corp"},
    )
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")

    assert '--management-group-id "contoso-conf-corp"' in sh
    assert '--management-group-id "contoso-conf-corp"' in ps1
    assert 'MG_ID="<your-mg-id>"' not in sh
    assert '$mgId = "<your-mg-id>"' not in ps1
    """policy-assignment / role-assignment inline their ``scope`` as a literal MG id.

    The scaffold engine writes the concrete target archetype id into
    ``scope`` at emit time. _plan_steps lifts that value through
    ``alias_map`` (brownfield rename) and binds it directly to the
    ``--management-group-id`` flag, eliminating the need for operators
    to retype a generic ``$MG_ID`` between assignments.
    """
    emitted = [
        {
            "template": "policy-assignment",
            "scope": "corp",
            "bicep": "bicep/policy-assignment.bicep",
            "params": "bicep/policy-assignment.parameters.json",
            "rollout_phase": "audit",
        },
        {
            "template": "role-assignment",
            "scope": "online",
            "bicep": "bicep/role-assignment.bicep",
            "params": "bicep/role-assignment.parameters.json",
            "rollout_phase": "audit",
        },
    ]
    write_deploy_script(
        out_dir=tmp_path,
        emitted=emitted,
        alias_map={"corp": "contoso-corp", "online": "contoso-online"},
    )
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")

    # Literal MG ids bound directly in the az deployment mg command.
    assert "--management-group-id \"contoso-corp\"" in sh
    assert "--management-group-id \"contoso-online\"" in sh
    assert "--management-group-id \"contoso-corp\"" in ps1
    assert "--management-group-id \"contoso-online\"" in ps1

    # Generic ``$MG_ID`` / ``$mgId`` is not declared because every step
    # resolved to a concrete literal.
    assert 'MG_ID="<your-mg-id>"' not in sh
    assert '$mgId = "<your-mg-id>"' not in ps1


def test_empty_params_falls_back_to_placeholders(tmp_path: Path) -> None:
    """Empty-findings runs (no params, no alias, no tenant) keep placeholders.

    The residual angle-bracket guard is still the final safety net when
    the scaffold phase has nothing to derive values from — e.g. a dry-run
    against an empty findings.json. The guard must still fire on the
    unfilled vars.
    """
    emitted = _mk_emitted(
        "management-groups",
        "alz-policy-definitions",
        "sovereignty-global-policies",
    )
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")

    assert 'LOCATION="<your-region>"' in sh
    assert '$location = "<your-region>"' in ps1
    assert 'SLZ_ROOT_MG_ID="<your-slz-root-mg-id>"' in sh
    assert 'TENANT_ROOT_MG_ID="<your-tenant-root-mg-id>"' in sh
    # Guard still fires on residual placeholders.
    assert "LOCATION still holds the placeholder" in sh
    assert "location still holds the placeholder" in ps1


# ---------------------------------------------------------------------------
# v0.15.0: per-step deploy + continue-on-error what-if
# ---------------------------------------------------------------------------


def test_apply_mode_interleaves_per_step(tmp_path: Path) -> None:
    """--apply branch pairs what-if + create for each step sequentially.

    In greenfield, templates 3+ target MGs created by template 1. The per-step
    pattern ensures management-groups is created before later steps try to
    what-if against those MGs.
    """
    emitted = _mk_emitted("management-groups", "log-analytics", "archetype-policies")
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")

    # Bash: the --apply branch contains per-step what-if→create pairs.
    apply_gate = sh.index('if [[ "$APPLY" == "true" ]]')
    else_gate = sh.index("else", apply_gate)
    apply_block = sh[apply_gate:else_gate]
    # management-groups: what-if appears before create within the block.
    mg_whatif = apply_block.index("management-groups")  # first mention is what-if
    mg_create_search = apply_block[mg_whatif + 1:]
    # After first management-groups mention (what-if), find "create" before next template
    assert "what-if" in apply_block[mg_whatif : mg_whatif + 300]
    assert "create" in mg_create_search[:600]

    # PS1: the -Apply branch contains per-step deploy pairs.
    ps1_apply_gate = ps1.index("if ($Apply)")
    ps1_else_gate = ps1.index("} else {", ps1_apply_gate)
    ps1_apply_block = ps1[ps1_apply_gate:ps1_else_gate]
    assert "management-groups" in ps1_apply_block
    assert "what-if" in ps1_apply_block
    assert "create" in ps1_apply_block


def test_whatif_only_continues_past_errors(tmp_path: Path) -> None:
    """What-if-only branch renders all steps with continue-on-error wrapping.

    Greenfield: steps targeting not-yet-created MGs fail, but the script
    continues to the next step and reports a summary.
    """
    emitted = _mk_emitted(
        "management-groups", "log-analytics", "alz-policy-definitions",
        "sovereignty-global-policies", "archetype-policies",
    )
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")

    # Bash: the what-if-only branch uses set +e and WHATIF_FAILED counter.
    assert "set +e" in sh
    assert "WHATIF_FAILED" in sh
    # Every emitted template appears in the what-if branch (not skipped on
    # first error).
    else_idx = sh.index("else")
    whatif_block = sh[else_idx:]
    for tmpl in ("management-groups", "log-analytics", "alz-policy-definitions",
                 "sovereignty-global-policies", "archetype-policies"):
        assert tmpl in whatif_block, f"{tmpl} missing from what-if block"
    # Summary line present.
    assert "succeeded" in whatif_block
    assert "failed" in whatif_block

    # PS1: the what-if-only branch uses try/catch and $whatifFailed counter.
    assert "$whatifFailed" in ps1
    ps1_else_idx = ps1.index("} else {")
    ps1_whatif_block = ps1[ps1_else_idx:]
    for tmpl in ("management-groups", "log-analytics", "alz-policy-definitions",
                 "sovereignty-global-policies", "archetype-policies"):
        assert tmpl in ps1_whatif_block, f"{tmpl} missing from ps1 what-if block"
    assert "succeeded" in ps1_whatif_block


def test_whatif_only_reports_greenfield_guidance(tmp_path: Path) -> None:
    """What-if summary tells greenfield operators to use --apply / -Apply."""
    emitted = _mk_emitted("management-groups", "archetype-policies")
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")
    assert "--apply" in sh
    assert "-Apply" in ps1


# ---------------------------------------------------------------------------
# Two-stage deploy with token refresh
# ---------------------------------------------------------------------------


def test_token_refresh_between_stages(tmp_path: Path) -> None:
    """--apply branch inserts az logout + az login between Stage 1 and Stage 2.

    After creating management groups (Stage 1), the cached Azure AD token
    doesn't include the new MGs. The token refresh ensures Stage 2 steps
    (policies, assignments) can target the freshly-created MGs.
    """
    emitted = _mk_emitted(
        "management-groups", "log-analytics", "alz-policy-definitions",
        "sovereignty-global-policies", "archetype-policies",
    )
    write_deploy_script(out_dir=tmp_path, emitted=emitted, tenant_id="test-tenant")
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")

    # Bash: Stage 1 → token refresh → Stage 2
    assert "Stage 1" in sh
    assert "Token refresh" in sh
    assert "Stage 2" in sh
    assert "az logout" in sh
    assert 'az login --tenant' in sh
    # Stage ordering: Stage 1 < token refresh < Stage 2
    s1_idx = sh.index("Stage 1")
    refresh_idx = sh.index("Token refresh")
    s2_idx = sh.index("Stage 2")
    assert s1_idx < refresh_idx < s2_idx

    # PS1: same structure
    assert "Stage 1" in ps1
    assert "Token refresh" in ps1
    assert "Stage 2" in ps1
    assert "az logout" in ps1
    assert "az login --tenant" in ps1
    ps1_s1 = ps1.index("Stage 1")
    ps1_refresh = ps1.index("Token refresh")
    ps1_s2 = ps1.index("Stage 2")
    assert ps1_s1 < ps1_refresh < ps1_s2


def test_stage1_only_skips_refresh(tmp_path: Path) -> None:
    """When only Stage 1 templates are emitted, no token refresh block appears.

    If there are no Stage 2 steps, the refresh is unnecessary and would just
    slow the operator down with an interactive login prompt.
    """
    emitted = _mk_emitted("management-groups", "log-analytics")
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")

    # No token refresh or Stage 2 when only Stage 1 templates are present.
    assert "Token refresh" not in sh
    assert "Stage 2" not in sh
    assert "Token refresh" not in ps1
    assert "Stage 2" not in ps1


def test_whatif_mode_has_no_token_refresh(tmp_path: Path) -> None:
    """What-if-only mode (no --apply) never includes a token refresh.

    No creates happen in what-if mode, so there's no stale token issue.
    """
    emitted = _mk_emitted(
        "management-groups", "alz-policy-definitions", "archetype-policies",
    )
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")

    # The what-if-only branch (else block) must not contain az logout/login.
    sh_else_idx = sh.index("else")
    sh_whatif_block = sh[sh_else_idx:]
    assert "az logout" not in sh_whatif_block
    assert "az login --tenant" not in sh_whatif_block

    ps1_else_idx = ps1.index("} else {")
    ps1_whatif_block = ps1[ps1_else_idx:]
    assert "az logout" not in ps1_whatif_block
    assert "az login --tenant" not in ps1_whatif_block
