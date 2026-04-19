"""Phase D tests — scaffold parameter pre-fill from findings + run_scope."""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from slz_readiness.scaffold import cli as scaffold_cli
from slz_readiness.scaffold.prefill import (
    classify_keys,
    merge_params,
    prefill_params,
    strip_engine_owned_fields,
)

_WS_FINDING = {
    "resource_type": "microsoft.operationalinsights/workspaces",
    "resource_id": "subscription/s1",
    "scope": "subscription/s1",
    "observed_state": {
        "workspaces": [
            {
                "name": "law-mgmt",
                "id": "/subscriptions/s1/.../law-mgmt",
                "resourceGroup": "rg-mgmt",
                "location": "westeurope",
                "subscriptionId": "s1",
            }
        ]
    },
}

_WS_FINDING_2 = {
    "resource_type": "microsoft.operationalinsights/workspaces",
    "resource_id": "subscription/s2",
    "scope": "subscription/s2",
    "observed_state": {
        "workspaces": [
            {
                "name": "law-extra",
                "id": "/subscriptions/s2/.../law-extra",
                "resourceGroup": "rg-extra",
                "location": "westeurope",
                "subscriptionId": "s2",
            }
        ]
    },
}


def test_prefill_management_groups_and_log_analytics() -> None:
    out = prefill_params(
        [_WS_FINDING],
        [],
        {"tenant_id": "tenant-guid"},
    )
    assert out["management-groups"] == {"parentManagementGroupId": "tenant-guid"}
    assert out["log-analytics"]["workspaceName"] == "law-mgmt"
    assert out["log-analytics"]["location"] == "westeurope"
    assert out["log-analytics"]["resourceGroupName"] == "rg-mgmt"
    assert out["archetype-policies"] == {"identityLocation": "westeurope"}
    assert out["sovereignty-global-policies"] == {"listOfAllowedLocations": ["westeurope"]}


def test_prefill_is_deterministic_across_permutations() -> None:
    a = prefill_params([_WS_FINDING, _WS_FINDING_2], [], {"tenant_id": "t"})
    b = prefill_params([_WS_FINDING_2, _WS_FINDING], [], {"tenant_id": "t"})
    assert a == b
    # Deterministic by (subscriptionId, id): s1 sorts before s2 → law-mgmt wins.
    assert a["log-analytics"]["workspaceName"] == "law-mgmt"


def test_prefill_region_tiebreak_alphabetical() -> None:
    # Two workspaces, two different regions, same count → alphabetical wins.
    f1 = {
        "resource_type": "microsoft.operationalinsights/workspaces",
        "observed_state": {
            "workspaces": [
                {"name": "a", "id": "/a", "location": "northeurope", "subscriptionId": "s"}
            ]
        },
    }
    f2 = {
        "resource_type": "microsoft.operationalinsights/workspaces",
        "observed_state": {
            "workspaces": [
                {"name": "b", "id": "/b", "location": "westeurope", "subscriptionId": "s"}
            ]
        },
    }
    out = prefill_params([f1, f2], [], {})
    assert out["archetype-policies"]["identityLocation"] == "northeurope"


def test_prefill_empty_findings_emits_minimal_keys() -> None:
    out = prefill_params([], [], {"tenant_id": "t"})
    # Only management-groups is derivable from run_scope alone.
    assert out == {"management-groups": {"parentManagementGroupId": "t"}}


def test_prefill_missing_tenant_and_findings_is_empty() -> None:
    assert prefill_params([], [], None) == {}


def test_prefill_h2_uses_observed_slz_parent_when_alias_present() -> None:
    """H2 (slz-demo run 20260419T120215Z): prefill MUST resolve the SLZ
    root's actual parent from observed MG hierarchy when alias_map["slz"]
    is set. Defaulting to tenant_id silently re-parents the SLZ root and
    discards intermediate MGs the operator already has.
    """
    mg_summary = {
        "resource_type": "microsoft.management/managementgroups.summary",
        "resource_id": "tenant",
        "scope": "/",
        "observed_state": {
            "present_ids": ["alz", "sucharda", "tenant-root"],
            "present_details": [
                {"id": "alz", "displayName": "ALZ", "parent_id": "sucharda"},
                {"id": "sucharda", "displayName": "Sucharda", "parent_id": "tenant-root"},
                {"id": "tenant-root", "displayName": "Tenant Root", "parent_id": None},
            ],
        },
    }
    out = prefill_params(
        [mg_summary],
        [],
        {"tenant_id": "tenant-root"},
        alias_map={"slz": "alz"},
    )
    # MUST be the observed parent, NOT the tenant id.
    # v0.12.0: alz is present in findings AND mapped from canonical slz,
    # so createSlz is auto-derived as False.
    assert out["management-groups"] == {
        "parentManagementGroupId": "sucharda",
        "createSlz": False,
    }


def test_prefill_h2_falls_back_to_tenant_id_in_greenfield() -> None:
    """No alias => greenfield => tenant_id is the legitimate default."""
    out = prefill_params([], [], {"tenant_id": "t"}, alias_map=None)
    assert out["management-groups"] == {"parentManagementGroupId": "t"}


def test_prefill_h2_alias_present_but_mg_not_in_findings_falls_back() -> None:
    """Alias points at a MG we have no observation for -> safe fallback to
    tenant_id (the engine still emits the param; operator must verify)."""
    out = prefill_params(
        [],
        [],
        {"tenant_id": "t"},
        alias_map={"slz": "alz"},
    )
    assert out["management-groups"] == {"parentManagementGroupId": "t"}


def test_strip_engine_owned_fields_removes_archetype_assignments() -> None:
    cleaned, warnings = strip_engine_owned_fields(
        {
            "archetype-policies": {
                "assignments": [{"name": "hack"}],
                "identityLocation": "westeurope",
            },
            "log-analytics": {"workspaceName": "keep"},
        }
    )
    assert "assignments" not in cleaned["archetype-policies"]
    assert cleaned["archetype-policies"]["identityLocation"] == "westeurope"
    assert cleaned["log-analytics"] == {"workspaceName": "keep"}
    assert any("archetype-policies.assignments" in w for w in warnings)


def test_merge_params_partial_override_preserves_prefilled_keys() -> None:
    prefilled = {
        "log-analytics": {
            "workspaceName": "law-mgmt",
            "location": "westeurope",
            "resourceGroupName": "rg-mgmt",
        }
    }
    user = {"log-analytics": {"retentionInDays": 730}}
    merged = merge_params(prefilled, user)
    assert merged["log-analytics"] == {
        "workspaceName": "law-mgmt",
        "location": "westeurope",
        "resourceGroupName": "rg-mgmt",
        "retentionInDays": 730,
    }


def test_classify_keys_marks_origin() -> None:
    prefilled = {"log-analytics": {"workspaceName": "a", "location": "b"}}
    user = {"log-analytics": {"location": "c", "retentionInDays": 30}}
    origin = classify_keys(prefilled, user)
    assert origin["log-analytics"]["workspaceName"] == "derived"
    assert origin["log-analytics"]["location"] == "operator_override"
    assert origin["log-analytics"]["retentionInDays"] == "operator_override"


def test_cli_params_optional_uses_prefill(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "findings.json").write_text(
        json.dumps(
            {
                "run_scope": {"tenant_id": "tenant-guid"},
                "findings": [_WS_FINDING],
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
    out_dir = tmp_path / "out"
    runner = CliRunner()
    # NO --params passed.
    result = runner.invoke(
        scaffold_cli.main,
        ["--gaps", str(gaps_path), "--out", str(out_dir)],
    )
    assert result.exit_code == 0, result.output
    auto = json.loads((out_dir / "scaffold.params.auto.json").read_text(encoding="utf-8"))
    pbt = auto["params_by_template"]
    assert pbt["sovereignty-global-policies"]["listOfAllowedLocations"] == ["westeurope"]
    # All keys for this run should be derived (no user overrides).
    origin = auto["key_origin"]["sovereignty-global-policies"]
    assert origin["listOfAllowedLocations"] == "derived"


def test_cli_engine_owned_override_is_stripped_with_warning(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "findings.json").write_text(
        json.dumps({"run_scope": {"tenant_id": "t"}, "findings": []}),
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
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    params_path = run_dir / "params.json"
    params_path.write_text(
        json.dumps(
            {
                "sovereignty-global-policies": {"listOfAllowedLocations": ["eastus2"]},
                "archetype-policies": {"assignments": [{"name": "hack"}]},
            }
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
    manifest = json.loads((out_dir / "scaffold.manifest.json").read_text(encoding="utf-8"))
    assert any(
        "archetype-policies.assignments" in w for w in manifest["warnings"]
    ), manifest["warnings"]


# -------------------- v0.12.1 location-input surfacing -------------------- #


def test_needs_operator_input_keys_flags_all_locations_when_prefill_empty() -> None:
    from slz_readiness.scaffold.prefill import needs_operator_input_keys

    # No workspaces in findings → modal_region None → no location prefills.
    prefilled = prefill_params([], [], {"tenant_id": "t"})
    missing = needs_operator_input_keys(prefilled, {})
    paths = {(e["template"], e["key"]) for e in missing}
    assert paths == {
        ("archetype-policies", "identityLocation"),
        ("sovereignty-global-policies", "listOfAllowedLocations"),
        ("sovereignty-confidential-policies", "listOfAllowedLocations"),
    }
    for entry in missing:
        assert entry["reason"] == "modal_region_unavailable"


def test_needs_operator_input_keys_empty_when_modal_region_available() -> None:
    from slz_readiness.scaffold.prefill import needs_operator_input_keys

    prefilled = prefill_params([_WS_FINDING], [], {"tenant_id": "t"})
    # Modal region derived → all three location keys are present.
    assert needs_operator_input_keys(prefilled, {}) == []


def test_needs_operator_input_keys_respects_operator_override() -> None:
    """Operator who supplied locations by hand short-circuits the prompt."""
    from slz_readiness.scaffold.prefill import needs_operator_input_keys

    prefilled = prefill_params([], [], {"tenant_id": "t"})
    user = {
        "archetype-policies": {"identityLocation": "eastus2"},
        "sovereignty-global-policies": {"listOfAllowedLocations": ["eastus2"]},
        "sovereignty-confidential-policies": {"listOfAllowedLocations": ["eastus2"]},
    }
    assert needs_operator_input_keys(prefilled, user) == []


def test_needs_operator_input_keys_deterministic_order() -> None:
    from slz_readiness.scaffold.prefill import needs_operator_input_keys

    missing = needs_operator_input_keys({}, {})
    assert [(e["template"], e["key"]) for e in missing] == sorted(
        (e["template"], e["key"]) for e in missing
    )


def test_cli_sidecar_surfaces_needs_operator_input(tmp_path: Path) -> None:
    """End-to-end: when findings carry no workspace location, the CLI
    writes needs_operator_input into scaffold.params.auto.json AND
    prepends a [location-input-required] warning to the manifest.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "findings.json").write_text(
        json.dumps({"run_scope": {"tenant_id": "t"}, "findings": []}),
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
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        scaffold_cli.main,
        ["--gaps", str(gaps_path), "--out", str(out_dir)],
    )
    assert result.exit_code == 0, result.output

    auto = json.loads(
        (out_dir / "scaffold.params.auto.json").read_text(encoding="utf-8")
    )
    missing = auto["needs_operator_input"]
    assert {(e["template"], e["key"]) for e in missing} == {
        ("archetype-policies", "identityLocation"),
        ("sovereignty-global-policies", "listOfAllowedLocations"),
        ("sovereignty-confidential-policies", "listOfAllowedLocations"),
    }

    manifest = json.loads(
        (out_dir / "scaffold.manifest.json").read_text(encoding="utf-8")
    )
    assert any(
        "[location-input-required]" in w for w in manifest["warnings"]
    ), manifest["warnings"]


def test_cli_sidecar_empty_needs_operator_input_when_workspaces_present(
    tmp_path: Path,
) -> None:
    """With a workspace carrying location, the sidecar's
    needs_operator_input is empty and no warning is emitted."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "findings.json").write_text(
        json.dumps(
            {"run_scope": {"tenant_id": "t"}, "findings": [_WS_FINDING]}
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
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        scaffold_cli.main,
        ["--gaps", str(gaps_path), "--out", str(out_dir)],
    )
    assert result.exit_code == 0, result.output

    auto = json.loads(
        (out_dir / "scaffold.params.auto.json").read_text(encoding="utf-8")
    )
    assert auto["needs_operator_input"] == []
    manifest = json.loads(
        (out_dir / "scaffold.manifest.json").read_text(encoding="utf-8")
    )
    assert not any(
        "[location-input-required]" in w for w in manifest["warnings"]
    )
