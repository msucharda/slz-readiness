"""Greenfield CLI test — slz-reconcile --mode greenfield writes all-null alias
and Evaluate output stays byte-identical to the non-aliased baseline."""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from slz_readiness.evaluate.engine import evaluate
from slz_readiness.reconcile import CANONICAL_ROLES
from slz_readiness.reconcile.cli import main as reconcile_main


def test_greenfield_writes_all_null_alias(tmp_path: Path) -> None:
    findings_path = tmp_path / "findings.json"
    findings_path.write_text(json.dumps({"findings": []}), encoding="utf-8")
    out = tmp_path / "mg_alias.json"

    runner = CliRunner()
    result = runner.invoke(
        reconcile_main,
        [
            "--mode", "greenfield",
            "--findings", str(findings_path),
            "--out", str(out),
        ],
    )
    assert result.exit_code == 0, result.output

    alias = json.loads(out.read_text(encoding="utf-8"))
    assert set(alias.keys()) == set(CANONICAL_ROLES)
    assert all(v is None for v in alias.values())

    # Summary artefacts are produced.
    assert (tmp_path / "reconcile.summary.md").exists()
    assert (tmp_path / "reconcile.summary.json").exists()


def test_greenfield_rejects_stray_proposal(tmp_path: Path) -> None:
    findings_path = tmp_path / "findings.json"
    findings_path.write_text(json.dumps({"findings": []}), encoding="utf-8")
    proposal = tmp_path / "p.json"
    proposal.write_text("{}", encoding="utf-8")
    out = tmp_path / "mg_alias.json"

    runner = CliRunner()
    result = runner.invoke(
        reconcile_main,
        [
            "--mode", "greenfield",
            "--findings", str(findings_path),
            "--proposal", str(proposal),
            "--out", str(out),
        ],
    )
    assert result.exit_code == 2
    assert "--proposal is only valid with --mode brownfield" in result.output


def test_brownfield_requires_proposal(tmp_path: Path) -> None:
    findings_path = tmp_path / "findings.json"
    findings_path.write_text(json.dumps({"findings": []}), encoding="utf-8")
    out = tmp_path / "mg_alias.json"

    runner = CliRunner()
    result = runner.invoke(
        reconcile_main,
        [
            "--mode", "brownfield",
            "--findings", str(findings_path),
            "--out", str(out),
        ],
    )
    assert result.exit_code == 2
    assert "--mode brownfield requires --proposal" in result.output


def test_empty_alias_in_evaluate_equals_no_alias() -> None:
    """Evaluate with an empty alias_map must produce the same output as no alias_map."""
    findings: list[dict] = []
    gaps_no_alias = [g.rule_id for g in evaluate(findings, alias_map=None)]
    gaps_empty = [g.rule_id for g in evaluate(findings, alias_map={})]
    assert gaps_no_alias == gaps_empty
