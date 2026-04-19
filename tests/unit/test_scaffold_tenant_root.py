"""Phase C tests — sovereignty-global-policies is emitted at tenant root.

Bug surfaced in slz-demo run 20260419T070007Z: the how-to-deploy.md
rendered all MG deployments with ``$mgId`` (the landing-zone MG id).
``sovereignty-global-policies`` must instead target the tenant-root
management group (whose id equals the tenant GUID), otherwise the
sovereign baseline is bound to the wrong scope and silently fails to
cover subscriptions outside that landing-zone MG.
"""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from slz_readiness.scaffold import cli as scaffold_cli
from slz_readiness.scaffold.cli import _deploy_commands


def _mk_emitted(
    template: str,
    scope: str = "tenant",
) -> dict:
    return {
        "template": template,
        "scope": scope,
        "bicep": f"bicep/{template}.bicep",
        "params": f"params/{template}.parameters.json",
        "rule_ids": [],
        "rollout_phase": "audit",
    }


def test_deploy_commands_sovereignty_global_uses_tenant_root_var() -> None:
    cmds = _deploy_commands(
        [_mk_emitted("sovereignty-global-policies")],
        tenant_id="00000000-1111-2222-3333-444444444444",
    )
    # Preamble contains the tenant-root variable with the observed tenant id.
    assert any(
        "$tenantRootMgId" in line and "00000000-1111-2222-3333-444444444444" in line
        for line in cmds["pwsh"]
    ), cmds["pwsh"]
    assert any(
        "TENANT_ROOT_MG_ID" in line and "00000000-1111-2222-3333-444444444444" in line
        for line in cmds["bash"]
    ), cmds["bash"]
    # Commands for sovereignty-global-policies reference tenantRootMgId, not mgId.
    global_lines = [
        line
        for line in cmds["pwsh"] + cmds["bash"]
        if "sovereignty-global-policies" in line
        or (
            "az deployment mg" in line
            and "tenantRootMgId" in line
            or "TENANT_ROOT_MG_ID" in line
        )
    ]
    # Must have at least one MG-scoped command using the tenant-root var
    assert any(
        "$tenantRootMgId" in line or "$TENANT_ROOT_MG_ID" in line for line in global_lines
    )
    # And NO line emits both sovereignty-global-policies invocation via $mgId.
    # Because _deploy_commands renders commands AFTER a "# sovereignty-..."
    # comment line, make sure no "az deployment mg ... $mgId" appears between
    # the comment and the next template block. A simpler assertion: every line
    # that contains "mg create" and "$mgId" must NOT be next to "sovereignty-global-policies".
    pwsh = cmds["pwsh"]
    for i, line in enumerate(pwsh):
        if "sovereignty-global-policies" in line:
            # Next ~6 lines are this block's deploy commands.
            block = pwsh[i : i + 7]
            assert not any(
                "--management-group-id $mgId" in b for b in block
            ), f"sovereignty-global-policies must NOT use $mgId: {block}"


def test_deploy_commands_other_mg_templates_still_use_mgid() -> None:
    """Only sovereignty-global-policies rewires to tenant root — archetype
    policies et al. continue to use $mgId (landing-zone scope)."""
    cmds = _deploy_commands(
        [_mk_emitted("archetype-policies", scope="scope:mg/corp")],
        tenant_id="00000000-1111-2222-3333-444444444444",
    )
    # No tenant-root preamble at all (no sov-global emitted).
    assert not any("tenantRootMgId" in line for line in cmds["pwsh"])
    assert not any("TENANT_ROOT_MG_ID" in line for line in cmds["bash"])
    # archetype-policies uses $mgId
    assert any(
        "--management-group-id $mgId" in line for line in cmds["pwsh"]
    ), cmds["pwsh"]


def test_deploy_commands_falls_back_to_placeholder_when_tenant_id_missing() -> None:
    cmds = _deploy_commands(
        [_mk_emitted("sovereignty-global-policies")],
        tenant_id=None,
    )
    assert any("<your-tenant-root-mg-id>" in line for line in cmds["pwsh"]), cmds["pwsh"]
    assert any("<your-tenant-root-mg-id>" in line for line in cmds["bash"]), cmds["bash"]


def test_main_plumbs_tenant_id_from_findings(tmp_path: Path) -> None:
    """End-to-end: `slz-scaffold` reads tenant_id from findings.json in the
    gaps-file directory and pre-fills how-to-deploy.md."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "findings.json").write_text(
        json.dumps(
            {
                "run_scope": {"tenant_id": "abc12345-6789-1234-5678-abcdefabcdef"},
                "findings": [],
            }
        ),
        encoding="utf-8",
    )
    gaps_path = run_dir / "gaps.json"
    gaps_path.write_text(
        json.dumps(
            {
                "gaps": [
                    {
                        "rule_id": "policy.slz.sovereign_root_policies_applied",
                        "severity": "high",
                        "status": "missing",
                        "resource_id": "tenant",
                        "observed": {},
                        "expected": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    params_path = run_dir / "params.json"
    params_path.write_text(
        json.dumps(
            {"sovereignty-global-policies": {"listOfAllowedLocations": ["westeurope"]}}
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        scaffold_cli.main,
        [
            "--gaps",
            str(gaps_path),
            "--params",
            str(params_path),
            "--out",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    how = (out_dir / "how-to-deploy.md").read_text(encoding="utf-8")
    assert "abc12345-6789-1234-5678-abcdefabcdef" in how
    assert "$tenantRootMgId" in how
    # And the sovereignty block's observe query must hit tenant root.
    assert "az policy state list --management-group $tenantRootMgId" in how
