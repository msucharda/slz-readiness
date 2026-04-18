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
