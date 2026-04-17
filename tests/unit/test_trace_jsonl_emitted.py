"""Asserts each phase CLI writes a trace.jsonl with at least one event."""
from __future__ import annotations

import json
from pathlib import Path

from slz_readiness.evaluate.engine import run as evaluate_run


def test_evaluate_writes_trace_jsonl(tmp_path: Path) -> None:
    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    findings_path = fixtures / "mostly_compliant.findings.json"
    gaps_path = tmp_path / "gaps.json"

    evaluate_run(findings_path, gaps_path)

    trace = tmp_path / "trace.jsonl"
    assert trace.exists(), "evaluate should produce a trace.jsonl next to gaps.json"
    lines = [line for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines, "trace.jsonl is empty"
    records = [json.loads(line) for line in lines]
    events = {r["event"] for r in records}
    assert "evaluate.begin" in events
    assert "evaluate.end" in events
    assert any(r.get("phase") == "evaluate" for r in records)
