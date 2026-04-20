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
