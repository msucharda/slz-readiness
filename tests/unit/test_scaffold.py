"""Unit tests for the scaffold engine."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from slz_readiness.scaffold.engine import ScaffoldError, scaffold_for_gaps


def _gap(rule_id: str, resource_id: str = "tenant", *, status: str = "missing") -> dict:
    return {
        "rule_id": rule_id,
        "severity": "high",
        "resource_id": resource_id,
        "status": status,
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


def test_scaffold_rejects_invalid_params(tmp_path: Path) -> None:
    gaps = [_gap("mg.slz.hierarchy_shape")]
    with pytest.raises(ScaffoldError):
        scaffold_for_gaps(gaps, {"management-groups": {}}, tmp_path)


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
