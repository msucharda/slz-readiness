"""Phase E tests — brownfield MG-move prerequisite block + present_details."""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from slz_readiness.discover import mg_hierarchy
from slz_readiness.scaffold import cli as scaffold_cli


def test_mg_hierarchy_observed_state_has_present_details(monkeypatch) -> None:
    """Discover emits ``present_details`` alongside ``present_ids``."""
    mgs = [
        {"name": "alz", "displayName": "ALZ Root"},
        {"name": "landingzones", "displayName": "Landing Zones"},
    ]

    def fake_list_mgs():
        return mgs

    def fake_show(name: str):
        return {
            "properties": {
                "details": {
                    "parent": {"name": "tenant-root"} if name == "alz" else {"name": "alz"}
                }
            }
        }

    monkeypatch.setattr(mg_hierarchy, "_list_mgs", fake_list_mgs)
    monkeypatch.setattr(mg_hierarchy, "run_az", lambda *a, **k: mgs)
    monkeypatch.setattr(mg_hierarchy, "_show_mg", fake_show)

    findings = mg_hierarchy.discover()
    state = findings[0]["observed_state"]
    assert state["present_ids"] == ["alz", "landingzones"]
    details_by_id = {d["id"]: d for d in state["present_details"]}
    assert details_by_id["alz"]["parent_id"] == "tenant-root"
    assert details_by_id["alz"]["displayName"] == "ALZ Root"
    assert details_by_id["landingzones"]["parent_id"] == "alz"


def test_mg_hierarchy_tolerates_missing_parent(monkeypatch) -> None:
    mgs = [{"name": "alz"}]
    monkeypatch.setattr(mg_hierarchy, "_list_mgs", lambda: mgs)
    monkeypatch.setattr(mg_hierarchy, "run_az", lambda *a, **k: mgs)
    monkeypatch.setattr(mg_hierarchy, "_show_mg", lambda name: None)

    findings = mg_hierarchy.discover()
    d = findings[0]["observed_state"]["present_details"][0]
    assert d["id"] == "alz"
    assert d["parent_id"] is None


def _invoke_scaffold(tmp_path: Path, mg_alias: dict[str, str | None] | None) -> str:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "findings.json").write_text(
        json.dumps({"run_scope": {"tenant_id": "t"}, "findings": []}),
        encoding="utf-8",
    )
    if mg_alias is not None:
        (run_dir / "mg_alias.json").write_text(
            json.dumps(mg_alias),
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
            {"sovereignty-global-policies": {"listOfAllowedLocations": ["eastus2"]}}
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
    return (out_dir / "how-to-deploy.md").read_text(encoding="utf-8")


def test_how_to_deploy_emits_move_block_when_alias_present(tmp_path: Path) -> None:
    how = _invoke_scaffold(tmp_path, {"landingzones": "alz"})
    assert "Prerequisites — brownfield MG moves" in how
    assert "az account management-group move" in how
    assert "immutable once the MG has been" in how


def test_how_to_deploy_no_move_block_without_alias(tmp_path: Path) -> None:
    how = _invoke_scaffold(tmp_path, None)
    assert "Prerequisites — brownfield MG moves" not in how
    assert "az account management-group move" not in how
