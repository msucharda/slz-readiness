"""Phase F — integration replay of the slz-demo gaps.json.

Asserts:

1. Re-running ``evaluate`` over ``findings.json`` with the Phase-A dedupe
   fix produces at most ONE gap for ``logging.management_la_workspace_exists``
   (previously produced one ``unknown`` + one ``missing`` — the bug).
2. Running ``scaffold`` against the deduped gaps.json does NOT emit
   ``log-analytics.bicep`` (because the surviving gap's status is
   ``unknown``, which ``_unscaffolded_gaps`` skips).

Skipped when the demo artifacts directory is absent (CI runs on a
vanilla checkout without the sibling ``slz-demo/`` tree).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from slz_readiness.evaluate.cli import main as evaluate_main
from slz_readiness.scaffold import cli as scaffold_cli

_DEMO_RUN = Path(r"C:\Users\msucharda\git\slz-demo\artifacts\20260419T070007Z")


@pytest.mark.skipif(
    not _DEMO_RUN.exists(), reason="slz-demo artifacts not present on this host"
)
def test_demo_replay_evaluate_deduplicates_la_workspace(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    # Copy findings.json so evaluate can read it via --findings.
    findings_src = _DEMO_RUN / "findings.json"
    findings_dst = out_dir / "findings.json"
    findings_dst.write_bytes(findings_src.read_bytes())
    runner = CliRunner()
    result = runner.invoke(
        evaluate_main,
        ["--findings", str(findings_dst), "--gaps", str(out_dir / "gaps.json")],
    )
    # evaluate returns 1 when gaps are present (CI signal) — both 0 and 1 are
    # "ran successfully". Only 2+ indicates a crash.
    assert result.exit_code in (0, 1), result.output
    gaps_doc = json.loads((out_dir / "gaps.json").read_text(encoding="utf-8"))
    gaps = gaps_doc.get("gaps", [])
    la_gaps = [g for g in gaps if g.get("rule_id") == "logging.management_la_workspace_exists"]
    # Phase A fix: no tenant-level missing duplicate when error findings exist.
    statuses = sorted({g.get("status") for g in la_gaps})
    # Must not contain BOTH "unknown" AND "missing" at the same resource_id.
    by_rid: dict[str, set[str]] = {}
    for g in la_gaps:
        by_rid.setdefault(g.get("resource_id", ""), set()).add(g.get("status"))
    for rid, stati in by_rid.items():
        assert not (
            "unknown" in stati and "missing" in stati
        ), f"dedupe failed for {rid}: {stati}"
    # Expect at least one unknown (the demo's error finding).
    assert "unknown" in statuses


@pytest.mark.skipif(
    not _DEMO_RUN.exists(), reason="slz-demo artifacts not present on this host"
)
def test_demo_replay_scaffold_does_not_emit_log_analytics(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    findings_dst = out_dir / "findings.json"
    findings_dst.write_bytes((_DEMO_RUN / "findings.json").read_bytes())
    gaps_dst = out_dir / "gaps.json"
    runner = CliRunner()
    r1 = runner.invoke(
        evaluate_main,
        ["--findings", str(findings_dst), "--gaps", str(gaps_dst)],
    )
    assert r1.exit_code in (0, 1), r1.output
    scaffold_out = out_dir / "scaffold"
    r2 = runner.invoke(
        scaffold_cli.main,
        ["--gaps", str(gaps_dst), "--out", str(scaffold_out)],
    )
    assert r2.exit_code == 0, r2.output
    manifest = json.loads(
        (scaffold_out / "scaffold.manifest.json").read_text(encoding="utf-8")
    )
    emitted_templates = {e.get("template") for e in manifest.get("emitted", [])}
    # Phase F: log-analytics must not be emitted when the only LA gap is unknown.
    assert "log-analytics" not in emitted_templates, manifest
