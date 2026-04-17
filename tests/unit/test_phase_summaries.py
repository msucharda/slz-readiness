"""Smoke + determinism tests for plan.summary_cli and evaluate/scaffold summary emission."""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from slz_readiness.evaluate import engine as evaluate_engine
from slz_readiness.plan import summary_cli as plan_summary_cli
from slz_readiness.scaffold import cli as scaffold_cli

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _run_evaluate(tmp_path: Path, fixture_name: str) -> Path:
    findings_path = FIXTURES / fixture_name
    gaps_path = tmp_path / "gaps.json"
    evaluate_engine.run(findings_path, gaps_path)
    return gaps_path


def test_evaluate_summary_is_deterministic(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    run_a.mkdir()
    run_b.mkdir()
    evaluate_engine.run(FIXTURES / "minimal_non_compliant.findings.json", run_a / "gaps.json")
    evaluate_engine.run(FIXTURES / "minimal_non_compliant.findings.json", run_b / "gaps.json")
    a = json.loads((run_a / "evaluate.summary.json").read_text(encoding="utf-8"))
    b = json.loads((run_b / "evaluate.summary.json").read_text(encoding="utf-8"))
    # ts may differ; everything else must be identical.
    a.pop("generated_at", None)
    b.pop("generated_at", None)
    assert a == b
    assert "gap_count" in a
    assert (run_a / "evaluate.summary.md").exists()


def test_plan_summary_cli_emits_artifacts(tmp_path):
    gaps_path = _run_evaluate(tmp_path, "minimal_non_compliant.findings.json")
    runner = CliRunner()
    result = runner.invoke(
        plan_summary_cli.main,
        ["--gaps", str(gaps_path), "--out-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    md = (tmp_path / "plan.summary.md").read_text(encoding="utf-8")
    assert "# SLZ Plan summary" in md
    assert "Readiness snapshot" in md
    assert "Order of operations" in md
    j = json.loads((tmp_path / "plan.summary.json").read_text(encoding="utf-8"))
    assert j["phase"] == "plan"
    assert "foundation" in j
    assert "by_severity" in j


def test_plan_summary_cli_deterministic(tmp_path):
    gaps_path = _run_evaluate(tmp_path, "mostly_compliant.findings.json")
    runner = CliRunner()
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    for out in (a, b):
        r = runner.invoke(
            plan_summary_cli.main,
            ["--gaps", str(gaps_path), "--out-dir", str(out)],
        )
        assert r.exit_code == 0, r.output
    ja = json.loads((a / "plan.summary.json").read_text(encoding="utf-8"))
    jb = json.loads((b / "plan.summary.json").read_text(encoding="utf-8"))
    assert ja == jb


def test_scaffold_summary_lists_emitted_and_unscaffolded(tmp_path):
    # Produce gaps + params via existing fixtures
    gaps_path = _run_evaluate(tmp_path, "minimal_non_compliant.findings.json")
    gaps = json.loads(gaps_path.read_text(encoding="utf-8"))["gaps"]

    # Build a minimal params file covering templates we expect to emit.
    params = {
        "management-groups": {
            "parentManagementGroupId": "00000000-0000-0000-0000-000000000000",
            "slzDisplayName": "Sovereign Landing Zone",
        },
        "log-analytics": {
            "workspaceName": "log-slz-mgmt",
            "location": "swedencentral",
            "retentionInDays": 365,
            "skuName": "PerGB2018",
        },
        "sovereignty-global-policies": {
            "enforcementMode": "Default",
            "listOfAllowedLocations": ["swedencentral"],
        },
        "sovereignty-confidential-policies": {"enforcementMode": "Default"},
    }
    params_path = tmp_path / "scaffold.params.json"
    params_path.write_text(json.dumps(params), encoding="utf-8")
    out = tmp_path / "run"
    runner = CliRunner()
    result = runner.invoke(
        scaffold_cli.main,
        [
            "--gaps",
            str(gaps_path),
            "--params",
            str(params_path),
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    j = json.loads((out / "scaffold.summary.json").read_text(encoding="utf-8"))
    assert j["phase"] == "scaffold"
    assert j["emitted_count"] >= 1
    md = (out / "scaffold.summary.md").read_text(encoding="utf-8")
    assert "Emitted templates" in md
    assert "Deployment commands" in md
    # Any unknown-status gap must appear in the not-scaffolded block.
    has_unknown = any(g.get("status") == "unknown" for g in gaps)
    if has_unknown:
        assert "Gaps NOT scaffolded" in md


def test_run_summary_rollup_concatenates(tmp_path):
    gaps_path = _run_evaluate(tmp_path, "minimal_non_compliant.findings.json")
    # Plan summary goes into tmp_path (same dir as gaps).
    CliRunner().invoke(
        plan_summary_cli.main,
        ["--gaps", str(gaps_path), "--out-dir", str(tmp_path)],
    )
    # Scaffold writes into its own --out dir; to exercise the rollup we put it
    # in the SAME dir as evaluate + plan outputs.
    params_path = tmp_path / "scaffold.params.json"
    params_path.write_text(
        json.dumps({
            "management-groups": {
                "parentManagementGroupId": "00000000-0000-0000-0000-000000000000",
                "slzDisplayName": "Sovereign Landing Zone",
            },
            "log-analytics": {
                "workspaceName": "log-slz-mgmt",
                "location": "swedencentral",
                "retentionInDays": 365,
                "skuName": "PerGB2018",
            },
            "sovereignty-global-policies": {
                "enforcementMode": "Default",
                "listOfAllowedLocations": ["swedencentral"],
            },
            "sovereignty-confidential-policies": {"enforcementMode": "Default"},
        }),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        scaffold_cli.main,
        [
            "--gaps",
            str(gaps_path),
            "--params",
            str(params_path),
            "--out",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    rollup = (tmp_path / "run.summary.md").read_text(encoding="utf-8")
    assert "# SLZ Run summary" in rollup
    assert "<!-- source: evaluate.summary.md -->" in rollup
    assert "<!-- source: plan.summary.md -->" in rollup
    assert "<!-- source: scaffold.summary.md -->" in rollup
