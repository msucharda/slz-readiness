"""Unit tests for the scaffold engine."""
from __future__ import annotations

import json
from pathlib import Path

from slz_readiness.scaffold.engine import (
    _downshift_deny_to_audit,
    scaffold_for_gaps,
)


def _gap(rule_id: str, resource_id: str = "tenant", *, status: str = "missing") -> dict:
    return {
        "rule_id": rule_id,
        "severity": "high",
        "resource_id": resource_id,
        "status": status,
    }


def _archetype_gap(rule_id: str, resource_id: str, path: str) -> dict:
    return {
        "rule_id": rule_id,
        "severity": "high",
        "resource_id": resource_id,
        "status": "missing",
        "baseline_ref": {
            "source": "https://github.com/Azure/Azure-Landing-Zones-Library",
            "path": path,
            "sha": "0000000000000000000000000000000000000000",
        },
        "observed": {"missing": []},
    }


def test_scaffold_emits_templates_for_gaps(tmp_path: Path) -> None:
    gaps = [
        _gap("mg.slz.hierarchy_shape"),
        _gap(
            "sovereignty.confidential_corp_policies_applied",
            "scope:mg/confidential_corp",
        ),
    ]
    params = {
        "management-groups": {"parentManagementGroupId": "00000000-0000-0000-0000-000000000000"},
        "sovereignty-confidential-policies": {"enforcementMode": "DoNotEnforce"},
    }
    emitted, warnings = scaffold_for_gaps(gaps, params, tmp_path)
    stems = sorted(e["template"] for e in emitted)
    assert stems == ["management-groups", "sovereignty-confidential-policies"]
    # Per-scope templates get a scope suffix in their filename.
    conf = next(e for e in emitted if e["template"] == "sovereignty-confidential-policies")
    assert conf["scope"] == "confidential_corp"
    assert "sovereignty-confidential-policies-confidential_corp.bicep" in conf["bicep"]
    for e in emitted:
        assert (tmp_path / e["bicep"]).exists()
        doc = json.loads((tmp_path / e["params"]).read_text(encoding="utf-8"))
        assert doc["$schema"].startswith("https://schema.management.azure.com/")
    assert isinstance(warnings, list)


def test_scaffold_demotes_invalid_params_to_warning(tmp_path: Path) -> None:
    """Fix 6: a single bad gap no longer aborts the whole scaffold run.

    Invalid params for one template surface as a warning with 'SKIPPED'; the
    function returns cleanly with no emit. (cli.main is what hard-fails when
    zero templates emit.)
    """
    gaps = [_gap("mg.slz.hierarchy_shape")]
    emitted, warnings = scaffold_for_gaps(gaps, {"management-groups": {}}, tmp_path)
    assert emitted == []
    assert any("SKIPPED" in w and "management-groups" in w for w in warnings), warnings


def test_scaffold_ignores_unmapped_rule_ids(tmp_path: Path) -> None:
    emitted, warnings = scaffold_for_gaps([_gap("something.not.mapped")], {}, tmp_path)
    assert emitted == []
    assert warnings == []


def test_scaffold_skips_unknown_status_gaps(tmp_path: Path) -> None:
    """status=unknown means discover couldn't verify; don't scaffold a fix."""
    gaps = [_gap("mg.slz.hierarchy_shape", status="unknown")]
    emitted, _ = scaffold_for_gaps(
        gaps,
        {"management-groups": {"parentManagementGroupId": "00000000-0000-0000-0000-000000000000"}},
        tmp_path,
    )
    assert emitted == []


def test_scaffold_dedup_is_per_scope(tmp_path: Path) -> None:
    """Two archetype gaps at different MGs must produce two Bicep files."""
    gaps = [
        {
            "rule_id": "archetype.alz_corp_policies_applied",
            "severity": "high",
            "resource_id": "scope:mg/corp",
            "status": "missing",
            "baseline_ref": {
                "source": "https://github.com/Azure/Azure-Landing-Zones-Library",
                "path": "platform/alz/archetype_definitions/corp.alz_archetype_definition.json",
                "sha": "4624a39edefd00b502d33d719ea0710837d32f5d",
            },
            "observed": {"missing": []},  # fall back to full list from archetype
        },
        {
            "rule_id": "archetype.alz_sandbox_policies_applied",
            "severity": "medium",
            "resource_id": "scope:mg/sandbox",
            "status": "missing",
            "baseline_ref": {
                "source": "https://github.com/Azure/Azure-Landing-Zones-Library",
                "path": "platform/alz/archetype_definitions/sandbox.alz_archetype_definition.json",
                "sha": "749741dbc54911b4b29b51fdb81bbb1f7bca29e6",
            },
            "observed": {"missing": []},
        },
    ]
    emitted, _ = scaffold_for_gaps(gaps, {}, tmp_path)
    scopes = sorted(e["scope"] for e in emitted)
    assert scopes == ["corp", "sandbox"]


# ---------------------------------------------------------------------------
# v0.3.0 — phased rollout (Audit → Enforce) and DINE identity propagation
# ---------------------------------------------------------------------------


def test_downshift_rewrites_deny_effect_to_audit() -> None:
    params = {
        "effect": {"value": "Deny"},
        "effectNotAllowedResources": {"value": "Deny"},
        "denyVnetPeering": {"value": "Deny"},  # name has no "effect" — preserved
        "listOfResourceTypesNotAllowed": {"value": ["SomeDeny"]},
    }
    out, n = _downshift_deny_to_audit(params)
    assert n == 2
    assert out["effect"] == {"value": "Audit"}
    assert out["effectNotAllowedResources"] == {"value": "Audit"}
    # Name doesn't contain "effect", preserved verbatim.
    assert out["denyVnetPeering"] == {"value": "Deny"}
    assert out["listOfResourceTypesNotAllowed"] == {"value": ["SomeDeny"]}


def test_downshift_preserves_non_deny_effects() -> None:
    params = {
        "effect": {"value": "DeployIfNotExists"},
        "appendEffect": {"value": "Append"},
        "disableEffect": {"value": "Disabled"},
        "someEffect": {"value": "Audit"},
    }
    out, n = _downshift_deny_to_audit(params)
    assert n == 0
    assert out == params  # identity (content-wise)


def test_phase_audit_is_default_and_rewrites_archetype_deny(tmp_path: Path) -> None:
    gaps = [
        _archetype_gap(
            "archetype.alz_corp_policies_applied",
            "scope:mg/corp",
            "platform/alz/archetype_definitions/corp.alz_archetype_definition.json",
        )
    ]
    emitted, warnings = scaffold_for_gaps(gaps, {}, tmp_path)
    assert len(emitted) == 1
    e = emitted[0]
    assert e["rollout_phase"] == "audit"
    params_doc = json.loads((tmp_path / e["params"]).read_text(encoding="utf-8"))
    assert params_doc["parameters"]["rolloutPhase"]["value"] == "audit"
    # At least one assignment's effect parameter must now be Audit (baseline had Deny).
    assignments = params_doc["parameters"]["assignments"]["value"]
    rewrote_something = False
    for a in assignments:
        for pname, pval in (a.get("parameters") or {}).items():
            if pname.lower().endswith("effect") and isinstance(pval, dict):
                assert pval.get("value") != "Deny", (
                    f"Assignment {a['name']} param {pname} still Deny under audit phase"
                )
                if pval.get("value") == "Audit":
                    rewrote_something = True
    assert rewrote_something, "Expected at least one Deny → Audit rewrite for corp archetype"
    # Phase-advisory warning emitted.
    assert any("rolloutPhase=audit" in w for w in warnings)


def test_phase_enforce_preserves_archetype_deny(tmp_path: Path) -> None:
    gaps = [
        _archetype_gap(
            "archetype.alz_corp_policies_applied",
            "scope:mg/corp",
            "platform/alz/archetype_definitions/corp.alz_archetype_definition.json",
        )
    ]
    emitted, warnings = scaffold_for_gaps(
        gaps, {"archetype-policies": {"rolloutPhase": "enforce"}}, tmp_path
    )
    e = emitted[0]
    assert e["rollout_phase"] == "enforce"
    params_doc = json.loads((tmp_path / e["params"]).read_text(encoding="utf-8"))
    assignments = params_doc["parameters"]["assignments"]["value"]
    # Some Deny effects must remain Deny under enforce.
    saw_deny = any(
        isinstance(pv, dict) and pv.get("value") == "Deny"
        for a in assignments
        for pn, pv in (a.get("parameters") or {}).items()
        if pn.lower().endswith("effect")
    )
    assert saw_deny, "Expected at least one Deny to survive under enforce phase"
    # Loud warning about blocking production on first deploy.
    assert any("rolloutPhase=enforce" in w and "Deny" in w for w in warnings)


def test_sovereign_global_phase_threaded_through_params(tmp_path: Path) -> None:
    gaps = [_gap("policy.slz.sovereign_root_policies_applied")]
    emitted, _ = scaffold_for_gaps(
        gaps,
        {
            "sovereignty-global-policies": {
                "rolloutPhase": "enforce",
                "listOfAllowedLocations": ["eastus2"],
            }
        },
        tmp_path,
    )
    assert len(emitted) == 1
    e = emitted[0]
    assert e["template"] == "sovereignty-global-policies"
    assert e["rollout_phase"] == "enforce"
    params_doc = json.loads((tmp_path / e["params"]).read_text(encoding="utf-8"))
    assert params_doc["parameters"]["rolloutPhase"]["value"] == "enforce"


def test_sovereign_confidential_defaults_phase_to_audit(tmp_path: Path) -> None:
    gaps = [_gap("sovereignty.confidential_corp_policies_applied", "scope:mg/confidential_corp")]
    emitted, _ = scaffold_for_gaps(gaps, {}, tmp_path)
    e = next(e for e in emitted if e["template"] == "sovereignty-confidential-policies")
    params_doc = json.loads((tmp_path / e["params"]).read_text(encoding="utf-8"))
    assert params_doc["parameters"]["rolloutPhase"]["value"] == "audit"


def test_archetype_marks_dine_assignments_identity_required(tmp_path: Path) -> None:
    gaps = [
        _archetype_gap(
            "archetype.alz_corp_policies_applied",
            "scope:mg/corp",
            "platform/alz/archetype_definitions/corp.alz_archetype_definition.json",
        )
    ]
    # Many DINE assignments in the corp archetype (Deploy-Private-DNS-Zones
    # etc.) ship with placeholder private-DNS-zone IDs. Use
    # include_placeholders=True so the DINE coverage assertion can see
    # them; default (skip) is verified elsewhere.
    emitted, warnings = scaffold_for_gaps(
        gaps, {}, tmp_path, include_placeholders=True
    )
    e = emitted[0]
    params_doc = json.loads((tmp_path / e["params"]).read_text(encoding="utf-8"))
    assignments = params_doc["parameters"]["assignments"]["value"]
    dine = [a for a in assignments if a.get("identityRequired")]
    # The ALZ corp archetype contains multiple Deploy-* / Enforce-GR-* assignments
    # that ship identity=SystemAssigned — at least one must be flagged.
    assert dine, "Expected at least one assignment with identityRequired=true"
    # Engine surfaces a DINE advisory warning.
    assert any("identity" in w.lower() and "roleDefinitionIds" in w for w in warnings)


def test_how_to_deploy_is_emitted_with_both_shells(tmp_path: Path) -> None:
    """CLI smoke: scaffold.summary.md has both shell fences and how-to-deploy.md exists."""
    from click.testing import CliRunner
    from slz_readiness.scaffold.cli import main

    gaps_path = tmp_path / "gaps.json"
    gaps_path.write_text(
        json.dumps(
            {
                "gaps": [
                    {
                        "rule_id": "policy.slz.sovereign_root_policies_applied",
                        "severity": "high",
                        "resource_id": "tenant",
                        "status": "missing",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    params_path = tmp_path / "params.json"
    params_path.write_text(
        json.dumps({"sovereignty-global-policies": {"listOfAllowedLocations": ["eastus2"]}}),
        encoding="utf-8",
    )
    out_dir = tmp_path / "run"
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--gaps", str(gaps_path), "--params", str(params_path), "--out", str(out_dir)],
    )
    assert result.exit_code == 0, result.output

    summary = (out_dir / "scaffold.summary.md").read_text(encoding="utf-8")
    assert "```powershell" in summary
    assert "```bash" in summary
    # slz-demo 20260419T120215Z / finding C3: sovereignty-global-policies
    # targets the SLZ root MG (``$slzRootMgId``), NOT the tenant root. The
    # previous ``$tenantRootMgId`` binding silently mis-scoped policies one
    # level too high.
    assert "$slzRootMgId" in summary
    assert "$SLZ_ROOT_MG_ID" in summary
    assert "$tenantRootMgId" not in summary
    # MG-scoped template must include --location (ARM requirement).
    assert '--location "$LOCATION"' in summary
    assert "--location $location" in summary

    how_to = (out_dir / "how-to-deploy.md").read_text(encoding="utf-8")
    assert "Wave 1 — Audit" in how_to
    assert "```powershell" in how_to
    assert "```bash" in how_to
    # Phased rollout recipe present.
    assert "rolloutPhase" in how_to
    # Prerequisites block declares the location variables.
    assert "$location" in how_to
    assert "LOCATION=" in how_to


def test_deploy_commands_are_scope_aware() -> None:
    """_deploy_commands emits the right az deployment verb + flags per scope.

    Regression for slz-demo session 7a48b1d3: MG-scoped template was emitted
    with `az deployment mg` but the template declared `targetScope = 'tenant'`
    (fixed to 'managementGroup'); subscription-scoped log-analytics was emitted
    with the same `az deployment mg` command, a latent scope mismatch. In
    v0.5.3 log-analytics flipped from resourceGroup to subscription scope
    (the template creates its own RG), so `az deployment sub` is correct.
    """
    from slz_readiness.scaffold.cli import _deploy_commands

    emitted = [
        {
            "template": "management-groups",
            "scope": "tenant",
            "bicep": "bicep/management-groups.bicep",
            "params": "params/management-groups.parameters.json",
        },
        {
            "template": "log-analytics",
            "scope": "mg/management",
            "bicep": "bicep/log-analytics.bicep",
            "params": "params/log-analytics.parameters.json",
        },
    ]
    cmds = _deploy_commands(emitted)
    bash = "\n".join(cmds["bash"])
    pwsh = "\n".join(cmds["pwsh"])

    # management-groups -> managementGroup scope: az deployment mg + --location
    assert 'az deployment mg what-if --management-group-id "$MG_ID" --location "$LOCATION"' in bash
    assert "az deployment mg what-if --management-group-id $mgId --location $location" in pwsh
    assert "az deployment mg create --management-group-id" in bash

    # log-analytics -> subscription scope: az deployment sub + --location
    assert 'az deployment sub what-if --location "$LOCATION"' in bash
    assert "az deployment sub what-if --location $location" in pwsh
    assert "az deployment sub create --location" in bash

    # No resource-group template emitted, so RG_NAME / $rgName MUST NOT appear.
    assert 'RG_NAME="<your-resource-group>"' not in bash
    assert "$rgName" not in pwsh

    # Both shells declare the MG / location vars.
    assert 'MG_ID="<your-mg-id>"' in bash
    assert 'LOCATION="<your-region>"' in bash
    assert '$mgId = "<your-mg-id>"' in pwsh
    assert '$location = "<your-region>"' in pwsh


def test_deploy_commands_omit_rg_when_no_rg_template() -> None:
    """When no resource-group template is emitted, RG_NAME/$rgName vars are not added."""
    from slz_readiness.scaffold.cli import _deploy_commands

    emitted = [
        {
            "template": "sovereignty-global-policies",
            "scope": "mg/slz",
            "bicep": "bicep/sovereignty-global-policies.bicep",
            "params": "params/sovereignty-global-policies.parameters.json",
        }
    ]
    cmds = _deploy_commands(emitted)
    assert 'RG_NAME="<your-resource-group>"' not in "\n".join(cmds["bash"])
    assert '$rgName' not in "\n".join(cmds["pwsh"])


def test_management_groups_template_scope_matches_deploy_command() -> None:
    """Lockstep check: the MG template's targetScope must match TEMPLATE_SCOPES.

    If this fails, the scaffold artifacts will fail at `az deployment … what-if`
    with: 'target scope "X" does not match the deployment scope "Y"'.
    """
    from slz_readiness.scaffold.template_registry import TEMPLATE_SCOPES

    template_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "scaffold"
        / "avm_templates"
        / "management-groups.bicep"
    )
    text = template_path.read_text(encoding="utf-8")
    assert "targetScope = 'managementGroup'" in text
    assert TEMPLATE_SCOPES["management-groups"] == "managementGroup"


def test_trace_records_rollout_phase(tmp_path: Path) -> None:
    gaps = [_gap("policy.slz.sovereign_root_policies_applied")]
    from slz_readiness import _trace

    # The engine only emits trace inside a tracer scope — wrap explicitly.
    with _trace.tracer(tmp_path, phase="scaffold"):
        scaffold_for_gaps(
            gaps,
            {"sovereignty-global-policies": {"listOfAllowedLocations": ["eastus2"]}},
            tmp_path,
        )
    trace = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    emits = [json.loads(line) for line in trace.splitlines() if '"template.emit"' in line]
    assert emits, "Expected at least one template.emit trace record"
    assert any(r.get("rollout_phase") == "audit" for r in emits)


def test_log_analytics_template_scope_matches_registry() -> None:
    """Regression for v0.5.3: template said 'subscription' but registry said
    'resourceGroup', so the emitted deploy command invoked `az deployment group`
    against a subscription-scoped Bicep -> ARM rejected the mismatch.
    """
    from slz_readiness.scaffold.template_registry import TEMPLATE_SCOPES

    template_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "scaffold"
        / "avm_templates"
        / "log-analytics.bicep"
    )
    text = template_path.read_text(encoding="utf-8")
    assert "targetScope = 'subscription'" in text
    assert TEMPLATE_SCOPES["log-analytics"] == "subscription"


def test_management_groups_emit_produces_runbooks(tmp_path: Path) -> None:
    """When management-groups is scaffolded, the low-priv runbooks are copied
    alongside it. They are the HITL escape hatch for operators without
    tenant-root RBAC (documented in how-to-deploy.md)."""
    gaps = [_gap("mg.slz.hierarchy_shape")]
    emitted, _ = scaffold_for_gaps(
        gaps,
        {"management-groups": {"parentManagementGroupId": "00000000-0000-0000-0000-000000000000"}},
        tmp_path,
    )
    assert len(emitted) == 1
    mg = emitted[0]
    assert mg["template"] == "management-groups"
    runbooks = mg.get("runbooks", [])
    assert sorted(runbooks) == [
        "runbooks/deploy-mg-hierarchy-lowpriv.ps1",
        "runbooks/deploy-mg-hierarchy-lowpriv.sh",
    ]
    for rb in runbooks:
        rb_path = tmp_path / rb
        assert rb_path.exists(), f"Runbook not written: {rb_path}"
        content = rb_path.read_text(encoding="utf-8")
        # Each runbook must PUT MG resources directly — that is the whole point.
        assert "Microsoft.Management/managementGroups" in content


def test_how_to_deploy_includes_lowpriv_section_when_mg_emitted(tmp_path: Path) -> None:
    """how-to-deploy.md must document the low-priv escape hatch whenever the
    management-groups template is scaffolded."""
    from slz_readiness.scaffold.cli import _write_how_to_deploy

    emitted = [
        {
            "template": "management-groups",
            "scope": "tenant",
            "bicep": "bicep/management-groups.bicep",
            "params": "params/management-groups.parameters.json",
            "runbooks": [
                "runbooks/deploy-mg-hierarchy-lowpriv.ps1",
                "runbooks/deploy-mg-hierarchy-lowpriv.sh",
            ],
        }
    ]
    _write_how_to_deploy(out_dir=tmp_path, emitted=emitted)
    how_to = (tmp_path / "how-to-deploy.md").read_text(encoding="utf-8")
    assert "When you lack tenant-scope deploy rights" in how_to
    assert "deploy-mg-hierarchy-lowpriv.ps1" in how_to
    assert "deploy-mg-hierarchy-lowpriv.sh" in how_to
    assert "AuthorizationFailed" in how_to


def test_all_rule_to_template_emits_compile(tmp_path: Path) -> None:
    """Synthesise a gap set covering every rule_id -> template mapping and
    assert each emitted Bicep file exists and has expected structure.
    Regression for BCP135 (v0.1.0..v0.5.2 shipped a non-compiling
    management-groups.bicep unnoticed because CI only compiled the template
    source, not emitted output).

    We skip archetype-* rules here because they require vendored baseline
    policy-assignment files already covered by dedicated archetype tests;
    the goal here is breadth of *template coverage*, not matcher re-testing.
    We also don't run `bicep build` inline — the dedicated whatif suite
    (tests/whatif/) covers that when azure/setup-bicep is available.
    """
    from slz_readiness.scaffold.template_registry import RULE_TO_TEMPLATE

    def _resource_id(rule_id: str) -> str:
        if rule_id == "sovereignty.confidential_corp_policies_applied":
            return "scope:mg/confidential_corp"
        if rule_id == "sovereignty.confidential_online_policies_applied":
            return "scope:mg/confidential_online"
        return "tenant"

    gaps = [
        _gap(rid, _resource_id(rid))
        for rid in sorted(RULE_TO_TEMPLATE)
        if not rid.startswith("archetype.")
    ]
    params = {
        "management-groups": {"parentManagementGroupId": "00000000-0000-0000-0000-000000000000"},
        "sovereignty-global-policies": {"listOfAllowedLocations": ["westeurope"]},
        "sovereignty-confidential-policies": {},
        "log-analytics": {"workspaceName": "la-slz-mgmt", "location": "westeurope"},
    }
    emitted, _ = scaffold_for_gaps(gaps, params, tmp_path)
    emitted_templates = {e["template"] for e in emitted}
    # Every non-archetype template must emit.
    expected = {
        RULE_TO_TEMPLATE[rid] for rid in RULE_TO_TEMPLATE if not rid.startswith("archetype.")
    }
    assert expected.issubset(emitted_templates), (
        f"Missing emits: {expected - emitted_templates}"
    )
    for e in emitted:
        bicep_path = tmp_path / e["bicep"]
        assert bicep_path.exists()
        text = bicep_path.read_text(encoding="utf-8")
        assert "targetScope" in text


# ---------------------------------------------------------------------------
# Fix 1 — log-analytics deploys at subscription scope with an RG resource
# ---------------------------------------------------------------------------


def test_log_analytics_template_is_subscription_scoped() -> None:
    """Regression for session aa4e7b2f: template was targetScope='resourceGroup',
    so `az deployment sub` / `az deployment group` deployments failed. The
    template now scopes at subscription and creates the RG it needs."""
    template_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "scaffold"
        / "avm_templates"
        / "log-analytics.bicep"
    )
    text = template_path.read_text(encoding="utf-8")
    assert "targetScope = 'subscription'" in text
    assert "Microsoft.Resources/resourceGroups@" in text
    assert "param resourceGroupName string" in text
    assert "scope: managementRg" in text


def test_log_analytics_schema_has_resource_group_name() -> None:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "scaffold"
        / "param_schemas"
        / "log-analytics.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert "resourceGroupName" in schema["properties"]
    assert "location" in schema["required"]


# ---------------------------------------------------------------------------
# Fix 4 — archetype-policies.bicep uses safe-access, not contains()
# ---------------------------------------------------------------------------


def test_archetype_policies_template_uses_safe_access() -> None:
    """Regression: `contains(a, 'X') ? a.X : default` triggers warnings and
    is replaced by Bicep safe-access `a.?X ?? default`."""
    template_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "scaffold"
        / "avm_templates"
        / "archetype-policies.bicep"
    )
    text = template_path.read_text(encoding="utf-8")
    assert "contains(a, '" not in text, (
        "archetype-policies.bicep still uses contains(a, '...') — "
        "switch to Bicep safe-access a.?X ?? default"
    )
    # Positive assertions: safe-access patterns present.
    assert "a.?identityRequired" in text
    assert "a.?enforcementMode" in text
    assert "a.?parameters" in text


# ---------------------------------------------------------------------------
# Fix 3 — DDoS / placeholder subscription-id detector
# ---------------------------------------------------------------------------


def test_placeholder_detector_matches_zero_guid_subscription() -> None:
    from slz_readiness.scaffold.engine import _contains_placeholder

    assert _contains_placeholder(
        "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/..."
    )
    zero_sub = "/subscriptions/00000000-0000-0000-0000-000000000000/x"
    assert _contains_placeholder({"value": zero_sub})
    assert _contains_placeholder({"value": "/placeholder/ddos-plan-id"})
    assert _contains_placeholder({"nested": {"value": ["real", zero_sub]}})
    # Negative: real-looking GUID must not match.
    assert not _contains_placeholder(
        "/subscriptions/11111111-2222-3333-4444-555555555555/resourceGroups/rg"
    )
    assert not _contains_placeholder({"value": "Audit"})
    # Negative: the word 'placeholder' in innocuous description text must not
    # fire — many ALZ policy defaultValue / metadata strings mention it.
    assert not _contains_placeholder({"value": "A placeholder for future use"})


def test_resolve_archetype_skips_placeholder_assignments_by_default(tmp_path: Path, monkeypatch) -> None:
    """Default behaviour (skip-by-default): a baseline assignment whose
    parameters still contain ALZ placeholders (all-zero subscription
    GUIDs, /placeholder/ segments) is SKIPPED — not emitted — and a loud
    warning names it. Emitting such assignments verbatim made
    ``az deployment … what-if`` fail with opaque validation errors
    (slz-demo run 20260419T070007Z). Use ``--include-placeholders`` to
    restore the legacy "emit verbatim" behaviour."""
    from slz_readiness.scaffold import engine as eng

    # Build a fake baseline tree: archetype + one good + one placeholder PA.
    baseline_root = tmp_path / "baseline"
    subtree = baseline_root / "platform" / "alz"
    pa_dir = subtree / "policy_assignments"
    pa_dir.mkdir(parents=True)
    arch_dir = subtree / "archetype_definitions"
    arch_dir.mkdir()
    arch_file = arch_dir / "fake.alz_archetype_definition.json"
    arch_file.write_text(
        json.dumps({"policy_assignments": ["Good-Assignment", "Placeholder-DDoS"]}),
        encoding="utf-8",
    )
    (pa_dir / "Good-Assignment.alz_policy_assignment.json").write_text(
        json.dumps(
            {
                "name": "Good-Assignment",
                "properties": {
                    "displayName": "Good",
                    "policyDefinitionId": "/providers/.../policyDefinitions/foo",
                    "parameters": {"effect": {"value": "Audit"}},
                },
            }
        ),
        encoding="utf-8",
    )
    (pa_dir / "Placeholder-DDoS.alz_policy_assignment.json").write_text(
        json.dumps(
            {
                "name": "Placeholder-DDoS",
                "properties": {
                    "displayName": "DDoS",
                    "policyDefinitionId": "/providers/.../policyDefinitions/ddos",
                    "parameters": {
                        "ddosPlan": {
                            "value": (
                                "/subscriptions/00000000-0000-0000-0000-000000000000"
                                "/resourceGroups/rg/providers/Microsoft.Network"
                                "/ddosProtectionPlans/plan"
                            )
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(eng, "BASELINE_DIR", baseline_root)
    eng._SUBTREE_FOR_ARCHETYPE_RULE["archetype.fake_policies_applied"] = "platform/alz"
    try:
        gap = {
            "rule_id": "archetype.fake_policies_applied",
            "baseline_ref": {
                "path": "platform/alz/archetype_definitions/fake.alz_archetype_definition.json"
            },
            "observed": {"missing": ["Good-Assignment", "Placeholder-DDoS"]},
        }
        assignments, warnings = eng._resolve_archetype_assignments(
            gap, rollout_phase="audit"
        )
        # Default: placeholder assignment SKIPPED.
        names = [a["name"] for a in assignments]
        assert names == ["Good-Assignment"], (
            f"Default must skip placeholder assignment; got {names}"
        )
        assert any(
            "SKIPPED" in w and "Placeholder-DDoS" in w and "placeholder" in w.lower()
            for w in warnings
        ), warnings
        assert any(
            "--include-placeholders" in w for w in warnings
        ), "Warning must point operator at the opt-in flag"

        # Opt-in: legacy "emit verbatim" behaviour preserved.
        assignments_incl, warnings_incl = eng._resolve_archetype_assignments(
            gap, rollout_phase="audit", include_placeholders=True
        )
        names_incl = [a["name"] for a in assignments_incl]
        assert set(names_incl) == {"Good-Assignment", "Placeholder-DDoS"}, names_incl
        assert any(
            "include-placeholders ON" in w.lower()
            or ("--include-placeholders on" in w.lower())
            for w in warnings_incl
        ), warnings_incl
        assert any("Placeholder-DDoS" in w for w in warnings_incl), warnings_incl
    finally:
        eng._SUBTREE_FOR_ARCHETYPE_RULE.pop("archetype.fake_policies_applied", None)
