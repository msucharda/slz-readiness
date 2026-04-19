"""Tests for v0.7.0 Scaffold brownfield surfacing — how-to-deploy.md
gains a retargeting block when ``mg_alias.json`` is non-empty, and the
scaffold engine emits warnings advertising defid-matched skip counts."""
from __future__ import annotations

import json
from pathlib import Path

from slz_readiness.scaffold.cli import _load_alias_for_doc, _write_how_to_deploy
from slz_readiness.scaffold.engine import scaffold_for_gaps


def _gap(rule_id: str, resource_id: str = "tenant") -> dict:
    return {
        "rule_id": rule_id,
        "severity": "high",
        "resource_id": resource_id,
        "status": "missing",
    }


def test_alias_loader_returns_only_non_null(tmp_path: Path) -> None:
    (tmp_path / "mg_alias.json").write_text(
        json.dumps({"corp": "acme-prod", "platform": None, "online": "  "}),
        encoding="utf-8",
    )
    assert _load_alias_for_doc(tmp_path) == {"corp": "acme-prod"}


def test_alias_loader_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _load_alias_for_doc(tmp_path) == {}
    assert _load_alias_for_doc(None) == {}


def test_how_to_deploy_includes_brownfield_block(tmp_path: Path) -> None:
    (tmp_path / "mg_alias.json").write_text(
        json.dumps({"corp": "acme-prod", "management": "acme-mgmt"}),
        encoding="utf-8",
    )
    out = tmp_path / "out"
    out.mkdir()
    emitted: list[dict] = [
        {
            "template": "archetype-policies",
            "scope": "corp",
            "bicep": "bicep/archetype-policies-corp.bicep",
            "params": "params/archetype-policies-corp.parameters.json",
            "rollout_phase": "audit",
            "rules": ["alz.corp_policies_applied"],
        }
    ]

    _write_how_to_deploy(out_dir=out, emitted=emitted, run_dir=tmp_path)

    doc = (out / "how-to-deploy.md").read_text(encoding="utf-8")
    assert "Brownfield retargeting" in doc
    assert "acme-prod" in doc
    assert "acme-mgmt" in doc
    assert "Canonical role" in doc


def test_how_to_deploy_omits_block_on_greenfield(tmp_path: Path) -> None:
    """No mg_alias.json → byte-for-byte parity with v0.6.0 doc shape."""
    out = tmp_path / "out"
    out.mkdir()
    emitted: list[dict] = [
        {
            "template": "management-groups",
            "scope": "tenant",
            "bicep": "bicep/management-groups.bicep",
            "params": "params/management-groups.parameters.json",
            "rollout_phase": "audit",
            "rules": ["mg.slz.hierarchy_shape"],
        }
    ]
    _write_how_to_deploy(out_dir=out, emitted=emitted, run_dir=tmp_path)
    doc = (out / "how-to-deploy.md").read_text(encoding="utf-8")
    assert "Brownfield retargeting" not in doc


def test_engine_emits_brownfield_warning(tmp_path: Path) -> None:
    """When scaffold_for_gaps sees mg_alias.json it prepends a warning
    so the operator knows aliases are in play before reading templates."""
    (tmp_path / "mg_alias.json").write_text(
        json.dumps({"corp": "acme-prod"}),
        encoding="utf-8",
    )
    _, warnings = scaffold_for_gaps(
        [_gap("mg.slz.hierarchy_shape")],
        {"management-groups": {}},
        tmp_path / "out",
        run_dir=tmp_path,
    )
    brownfield_warnings = [w for w in warnings if "brownfield" in w]
    assert brownfield_warnings, f"no brownfield warning emitted; got: {warnings}"
    assert "corp→acme-prod" in brownfield_warnings[0]


def test_engine_greenfield_no_brownfield_warning(tmp_path: Path) -> None:
    _, warnings = scaffold_for_gaps(
        [_gap("mg.slz.hierarchy_shape")],
        {"management-groups": {}},
        tmp_path / "out",
    )
    assert not [w for w in warnings if "brownfield" in w]


# -------------------- v0.8.0 Track α (rewrite-names) -------------------- #


def test_rewrite_names_off_emits_canonical_bicep(tmp_path: Path) -> None:
    """Default --rewrite-names=False: emitted Bicep is byte-identical to
    the vendored template (v0.7.x contract)."""
    (tmp_path / "mg_alias.json").write_text(
        json.dumps({"corp": "acme-corp-mg", "landingzones": "acme-lz"}),
        encoding="utf-8",
    )
    out = tmp_path / "out"
    emitted, _ = scaffold_for_gaps(
        [_gap("mg.slz.hierarchy_shape")],
        {"management-groups": {"parentManagementGroupId": "tenant-root"}},
        out,
        run_dir=tmp_path,
        rewrite_names=False,
    )
    assert emitted, "management-groups template must emit"
    bicep = (out / "bicep" / "management-groups.bicep").read_text(encoding="utf-8")
    # Canonical names retained because rewrite is off.
    assert "name: 'corp'" in bicep
    assert "name: 'landingzones'" in bicep
    assert "acme-corp-mg" not in bicep
    # And no name_substitutions key leaks into the manifest.
    assert "name_substitutions" not in emitted[0]


def test_rewrite_names_on_substitutes_tenant_names(tmp_path: Path) -> None:
    """--rewrite-names=True + non-null mg_alias.json: management-groups
    Bicep has tenant MG names in `name: '<role>'` properties."""
    (tmp_path / "mg_alias.json").write_text(
        json.dumps({"corp": "acme-corp-mg", "landingzones": "acme-lz"}),
        encoding="utf-8",
    )
    out = tmp_path / "out"
    emitted, warnings = scaffold_for_gaps(
        [_gap("mg.slz.hierarchy_shape")],
        {"management-groups": {"parentManagementGroupId": "tenant-root"}},
        out,
        run_dir=tmp_path,
        rewrite_names=True,
    )
    bicep = (out / "bicep" / "management-groups.bicep").read_text(encoding="utf-8")
    assert "name: 'acme-corp-mg'" in bicep
    assert "name: 'acme-lz'" in bicep
    # Unaliased roles keep canonical names.
    assert "name: 'management'" in bicep
    assert "name: 'platform'" in bicep
    # Original canonical aliased roles are gone.
    assert "name: 'corp'" not in bicep
    assert "name: 'landingzones'" not in bicep
    # tenantResourceId() call sites for aliased roles also rewritten
    # (v0.12.1): `var landingzonesId = tenantResourceId(..., 'acme-lz')`
    # so children reference the aliased parent, not the canonical one.
    assert "tenantResourceId('Microsoft.Management/managementGroups', 'acme-lz')" in bicep
    assert "tenantResourceId('Microsoft.Management/managementGroups', 'landingzones')" not in bicep
    # Manifest records the substitution count: 2 `name:` sites
    # (corp, landingzones) + 1 tenantResourceId site (landingzones) = 3.
    assert emitted[0].get("name_substitutions") == 3
    # Warning advertises the opt-in.
    assert any("--rewrite-names ON" in w for w in warnings)


def test_rewrite_names_preserves_symbolic_identifiers_and_comments(tmp_path: Path) -> None:
    """Rewrite targets `name: '<role>'` only. Bicep symbolic names (e.g.
    ``resource corp …``), comments, and displayNames stay canonical."""
    (tmp_path / "mg_alias.json").write_text(
        json.dumps({"corp": "acme-corp"}), encoding="utf-8"
    )
    out = tmp_path / "out"
    scaffold_for_gaps(
        [_gap("mg.slz.hierarchy_shape")],
        {"management-groups": {"parentManagementGroupId": "tenant-root"}},
        out,
        run_dir=tmp_path,
        rewrite_names=True,
    )
    bicep = (out / "bicep" / "management-groups.bicep").read_text(encoding="utf-8")
    # Bicep symbolic identifier is untouched — it lives before the
    # `name:` property, so the regex does not match it.
    assert "resource corp 'Microsoft.Management/managementGroups" in bicep
    # displayName 'Corp' unchanged.
    assert "displayName: 'Corp'" in bicep


def test_rewrite_names_on_with_empty_alias_is_noop(tmp_path: Path) -> None:
    """--rewrite-names=True but mg_alias.json missing: behaves as greenfield."""
    out = tmp_path / "out"
    emitted, warnings = scaffold_for_gaps(
        [_gap("mg.slz.hierarchy_shape")],
        {"management-groups": {"parentManagementGroupId": "tenant-root"}},
        out,
        run_dir=tmp_path,  # no mg_alias.json here
        rewrite_names=True,
    )
    bicep = (out / "bicep" / "management-groups.bicep").read_text(encoding="utf-8")
    assert "name: 'corp'" in bicep
    assert "name_substitutions" not in emitted[0]
    # Advisory warning about the empty alias map.
    assert any("empty or" in w for w in warnings)


def test_how_to_deploy_rewrite_mode_shows_apply_ready_note(tmp_path: Path) -> None:
    (tmp_path / "mg_alias.json").write_text(
        json.dumps({"corp": "acme-corp"}), encoding="utf-8"
    )
    out = tmp_path / "out"
    out.mkdir()
    emitted: list[dict] = [
        {
            "template": "management-groups",
            "scope": "tenant",
            "bicep": "bicep/management-groups.bicep",
            "params": "params/management-groups.parameters.json",
            "rollout_phase": "audit",
            "rules": ["mg.slz.hierarchy_shape"],
        }
    ]
    _write_how_to_deploy(
        out_dir=out, emitted=emitted, run_dir=tmp_path, rewrite_names=True
    )
    doc = (out / "how-to-deploy.md").read_text(encoding="utf-8")
    assert "apply-ready" in doc.lower()
    assert "acme-corp" in doc


def test_greenfield_parity_rewrite_names_with_all_null_alias(tmp_path: Path) -> None:
    """Greenfield parity for Track α: rewrite_names=True with an all-null
    mg_alias.json must behave identically to rewrite_names=False."""
    from slz_readiness.reconcile import CANONICAL_ROLES

    (tmp_path / "mg_alias.json").write_text(
        json.dumps({role: None for role in CANONICAL_ROLES}),
        encoding="utf-8",
    )
    out_on = tmp_path / "out_on"
    out_off = tmp_path / "out_off"
    scaffold_for_gaps(
        [_gap("mg.slz.hierarchy_shape")],
        {"management-groups": {"parentManagementGroupId": "tenant-root"}},
        out_on,
        run_dir=tmp_path,
        rewrite_names=True,
    )
    scaffold_for_gaps(
        [_gap("mg.slz.hierarchy_shape")],
        {"management-groups": {"parentManagementGroupId": "tenant-root"}},
        out_off,
        run_dir=tmp_path,
        rewrite_names=False,
    )
    bicep_on = (out_on / "bicep" / "management-groups.bicep").read_text(encoding="utf-8")
    bicep_off = (out_off / "bicep" / "management-groups.bicep").read_text(encoding="utf-8")
    assert bicep_on == bicep_off


# -------------------- v0.12.1 tenantResourceId() rewrite -------------------- #


def test_rewrite_names_fixes_tenant_resource_id_parent_refs(tmp_path: Path) -> None:
    """v0.12.1 regression: when the SLZ root and landing-zones roots are
    aliased (e.g. ``slz→alz``, ``landingzones→workloads``), both the
    ``name:`` property AND the ``tenantResourceId(...)`` call that child
    MGs use to compute parent ids must be rewritten. Otherwise `az
    deployment mg create` fails with ParentManagementGroupNotFound
    because `/managementGroups/slz` does not exist on the tenant.
    """
    (tmp_path / "mg_alias.json").write_text(
        json.dumps({"slz": "alz", "landingzones": "workloads"}),
        encoding="utf-8",
    )
    out = tmp_path / "out"
    emitted, _ = scaffold_for_gaps(
        [_gap("mg.slz.hierarchy_shape")],
        {"management-groups": {"parentManagementGroupId": "tenant-root"}},
        out,
        run_dir=tmp_path,
        rewrite_names=True,
    )
    bicep = (out / "bicep" / "management-groups.bicep").read_text(encoding="utf-8")

    # Resource names rewritten.
    assert "name: 'alz'" in bicep
    assert "name: 'workloads'" in bicep
    assert "name: 'slz'" not in bicep
    assert "name: 'landingzones'" not in bicep

    # tenantResourceId parent-id vars rewritten too — the core of the bug.
    assert "tenantResourceId('Microsoft.Management/managementGroups', 'alz')" in bicep
    assert (
        "tenantResourceId('Microsoft.Management/managementGroups', 'workloads')"
        in bicep
    )
    # Canonical tenantResourceId references must be gone.
    assert "tenantResourceId('Microsoft.Management/managementGroups', 'slz')" not in bicep
    assert (
        "tenantResourceId('Microsoft.Management/managementGroups', 'landingzones')"
        not in bicep
    )
    # Unaliased 'platform' tenantResourceId is untouched.
    assert (
        "tenantResourceId('Microsoft.Management/managementGroups', 'platform')" in bicep
    )

    # Substitution count: 2 `name:` (slz, landingzones) + 2 tenantResourceId
    # (slz, landingzones). `platform` is unaliased so its tenantResourceId
    # call is not counted.
    assert emitted[0].get("name_substitutions") == 4


def test_rewrite_names_off_leaves_tenant_resource_id_untouched(tmp_path: Path) -> None:
    """Negative: with rewrite OFF, the tenantResourceId() calls remain
    canonical even when a non-null alias map is present."""
    (tmp_path / "mg_alias.json").write_text(
        json.dumps({"slz": "alz"}), encoding="utf-8"
    )
    out = tmp_path / "out"
    scaffold_for_gaps(
        [_gap("mg.slz.hierarchy_shape")],
        {"management-groups": {"parentManagementGroupId": "tenant-root"}},
        out,
        run_dir=tmp_path,
        rewrite_names=False,
    )
    bicep = (out / "bicep" / "management-groups.bicep").read_text(encoding="utf-8")
    assert "tenantResourceId('Microsoft.Management/managementGroups', 'slz')" in bicep
    assert "tenantResourceId('Microsoft.Management/managementGroups', 'alz')" not in bicep
