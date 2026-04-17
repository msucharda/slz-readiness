"""Unit tests for the scaffold engine."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from slz_readiness.scaffold.engine import ScaffoldError, scaffold_for_gaps


def _gaps(*rule_ids: str) -> list[dict]:
    return [{"rule_id": rid, "severity": "high"} for rid in rule_ids]


def test_scaffold_emits_templates_for_gaps(tmp_path: Path) -> None:
    gaps = _gaps("mg.slz.hierarchy_shape", "sovereignty.confidential_corp_policies_applied")
    params = {
        "management-groups": {"parentManagementGroupId": "00000000-0000-0000-0000-000000000000"},
        "sovereignty-policies": {"deployConfidential": True, "deployGlobal": True},
    }
    emitted = scaffold_for_gaps(gaps, params, tmp_path)
    stems = sorted(e["template"] for e in emitted)
    assert stems == ["management-groups", "sovereignty-policies"]
    for e in emitted:
        assert (tmp_path / e["bicep"]).exists()
        doc = json.loads((tmp_path / e["params"]).read_text(encoding="utf-8"))
        assert doc["$schema"].startswith("https://schema.management.azure.com/")


def test_scaffold_rejects_invalid_params(tmp_path: Path) -> None:
    gaps = _gaps("mg.slz.hierarchy_shape")
    with pytest.raises(ScaffoldError):
        scaffold_for_gaps(gaps, {"management-groups": {}}, tmp_path)


def test_scaffold_ignores_unmapped_rule_ids(tmp_path: Path) -> None:
    emitted = scaffold_for_gaps(_gaps("something.not.mapped"), {}, tmp_path)
    assert emitted == []
