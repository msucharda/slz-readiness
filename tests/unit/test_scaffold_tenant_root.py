"""C3/H1 tests — sovereignty-global-policies targets the *SLZ* root MG.

Original bug (slz-demo run 20260419T070007Z): scaffold bound
``sovereignty-global-policies`` to the landing-zone MG (``$mgId``).
Over-correction (slz-demo run 20260419T120215Z, finding C3): scaffold
bound it to the **tenant** root MG (``$tenantRootMgId``), which is one
level too high — the SLZ root MG (alias ``slz``) is a *child* of the
tenant root, and a deployment at tenant root inherits policy over
siblings the SLZ does not own.

Corrected contract: sovereignty-global-policies always binds to the
``slz`` alias from ``mg_alias.json``. When the alias is unresolved the
command renders a loud placeholder and an explanatory note — never the
tenant GUID.
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


def test_deploy_commands_sovereignty_global_uses_slz_alias() -> None:
    """With an ``slz`` alias present, the preamble binds SLZ_ROOT_MG_ID to it."""
    cmds = _deploy_commands(
        [_mk_emitted("sovereignty-global-policies")],
        alias_map={"slz": "alz"},
    )
    # Preamble declares SLZ_ROOT_MG_ID with the aliased value (``alz``).
    assert any(
        "$slzRootMgId" in line and '"alz"' in line for line in cmds["pwsh"]
    ), cmds["pwsh"]
    assert any(
        "SLZ_ROOT_MG_ID" in line and '"alz"' in line for line in cmds["bash"]
    ), cmds["bash"]
    # The deployment command for sovereignty-global-policies references the
    # SLZ-root variable, NOT $mgId and NOT tenantRootMgId.
    pwsh = cmds["pwsh"]
    for i, line in enumerate(pwsh):
        if "sovereignty-global-policies" in line:
            block = pwsh[i : i + 7]
            assert any(
                "--management-group-id $slzRootMgId" in b for b in block
            ), f"expected SLZ-root binding, got: {block}"
            assert not any("--management-group-id $mgId" in b for b in block)
            assert not any("tenantRootMgId" in b for b in block)


def test_deploy_commands_sovereignty_global_without_alias_renders_placeholder() -> None:
    """No alias => ``<your-slz-root-mg-id>`` placeholder + loud warning note."""
    cmds = _deploy_commands(
        [_mk_emitted("sovereignty-global-policies")],
        alias_map={},
    )
    assert any(
        "<your-slz-root-mg-id>" in line for line in cmds["pwsh"]
    ), cmds["pwsh"]
    assert any(
        "<your-slz-root-mg-id>" in line for line in cmds["bash"]
    ), cmds["bash"]
    # A warning note tells the operator this is NOT the tenant root.
    assert any(
        "NOT the tenant root" in line for line in cmds["pwsh"] + cmds["bash"]
    )


def test_deploy_commands_tenant_id_is_not_used_for_sovereign_root() -> None:
    """Backcompat ``tenant_id`` MUST NOT populate SLZ_ROOT_MG_ID.

    Guards against regression of the C3 bug where the tenant root GUID
    was silently substituted as the sovereign-root target.
    """
    cmds = _deploy_commands(
        [_mk_emitted("sovereignty-global-policies")],
        tenant_id="00000000-1111-2222-3333-444444444444",
        alias_map=None,
    )
    # Tenant id MUST NOT appear anywhere — with no alias, placeholder is used.
    for line in cmds["pwsh"] + cmds["bash"]:
        assert "00000000-1111-2222-3333-444444444444" not in line


def test_deploy_commands_other_mg_templates_still_use_mgid() -> None:
    """Only sovereignty-* templates rewire; archetype-policies uses $mgId."""
    cmds = _deploy_commands(
        [_mk_emitted("archetype-policies", scope="scope:mg/corp")],
        alias_map={"corp": "corp"},
    )
    # No SLZ-root preamble at all (no sov-global emitted).
    assert not any("slzRootMgId" in line for line in cmds["pwsh"])
    assert not any("SLZ_ROOT_MG_ID" in line for line in cmds["bash"])
    # archetype-policies uses $mgId.
    assert any(
        "--management-group-id $mgId" in line for line in cmds["pwsh"]
    ), cmds["pwsh"]


def test_deploy_commands_sovereignty_confidential_uses_per_scope_alias() -> None:
    """H1: each confidential emission resolves to its own MG via alias_map."""
    cmds = _deploy_commands(
        [
            _mk_emitted("sovereignty-confidential-policies", scope="confidential_corp"),
            _mk_emitted("sovereignty-confidential-policies", scope="confidential_online"),
        ],
        alias_map={
            "confidential_corp": "conf-corp-mg",
            "confidential_online": "conf-online-mg",
        },
    )
    pwsh = "\n".join(cmds["pwsh"])
    # Both resolved MG names appear.
    assert '"conf-corp-mg"' in pwsh
    assert '"conf-online-mg"' in pwsh
    # Both scopes get their own labelled section.
    assert "confidential_corp -> conf-corp-mg" in pwsh
    assert "confidential_online -> conf-online-mg" in pwsh


def test_main_does_not_use_tenant_id_for_sovereign_root(tmp_path: Path) -> None:
    """End-to-end: findings.json tenant_id MUST NOT leak into the sovereign-
    root binding, even when no alias file exists.
    """
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
    # Tenant GUID MUST NOT appear as the sovereign-root target.
    assert "abc12345-6789-1234-5678-abcdefabcdef" not in how
    # Instead, the SLZ-root variable is referenced.
    assert "$slzRootMgId" in how
    # And the loud placeholder/note is present (no alias file in this run).
    assert "<your-slz-root-mg-id>" in how
    assert "NOT the tenant root" in how

