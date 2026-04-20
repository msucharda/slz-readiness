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
    """For every template, what-if must appear before create in the rendered file."""
    emitted = _mk_emitted("management-groups", "log-analytics", "archetype-policies")
    write_deploy_script(out_dir=tmp_path, emitted=emitted)
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    # what-if pass is emitted as a block before the APPLY gate; create pass after it.
    assert sh.index("what-if pass") < sh.index("create pass")
    # APPLY gate must sit between the two — i.e., the bash script must early-return
    # on what-if-only mode so the create block is only reached under --apply.
    gate = sh.index('if [[ "$APPLY" != "true" ]]')
    assert sh.index("what-if pass") < gate < sh.index("create pass")


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
    ]
    alias_map = {
        "slz": "alz",
        "confidential_corp": "conf-corp",
        "corp": "corp",
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
    sh_mg_idx = sh.index("management-groups")
    sh_mg_block = sh[sh_mg_idx : sh_mg_idx + 800]
    assert "$TENANT_ROOT_MG_ID" in sh_mg_block
    # ...and not the plain $MG_ID
    assert "--management-group-id \"$MG_ID\"" not in sh_mg_block

    ps1_mg_idx = ps1.index("management-groups")
    ps1_mg_block = ps1[ps1_mg_idx : ps1_mg_idx + 800]
    assert "$tenantRootMgId" in ps1_mg_block


def test_tenant_id_never_inlined(tmp_path: Path) -> None:
    """Tenant GUIDs must never leak into the emitted scripts.

    Regression: prior versions inlined ``tenant_id`` as the value of
    ``TENANT_EXPECTED`` / ``$tenantExpected``, making the generated
    scripts non-shareable (disclosed the customer tenant id).
    """
    emitted = _mk_emitted("management-groups")
    secret_tenant = "99554ba8-f985-4a2d-be21-fc3a62570dd4"
    write_deploy_script(
        out_dir=tmp_path,
        emitted=emitted,
        tenant_id=secret_tenant,
    )
    sh = (tmp_path / "runbooks" / "deploy-all.sh").read_text(encoding="utf-8")
    ps1 = (tmp_path / "runbooks" / "deploy-all.ps1").read_text(encoding="utf-8")
    assert secret_tenant not in sh
    assert secret_tenant not in ps1
    assert 'TENANT_EXPECTED="<tenant-id>"' in sh
    assert '$tenantExpected = "<tenant-id>"' in ps1


def test_placeholder_guard_blocks_unedited_vars(tmp_path: Path) -> None:
    """Unedited ``<your-*>`` placeholders must fail-fast before any ``az`` call.

    Regression: Windows ``cmd.exe`` (which wraps ``az.cmd``) interprets ``<``
    in an argument as input redirection, masking a 12-step catastrophic
    failure as phantom success ("The system cannot find the file specified.").
    The script must refuse to proceed while any declared variable still holds
    its ``<...>`` placeholder.
    """
    emitted = _mk_emitted("management-groups", "archetype-policies")
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
