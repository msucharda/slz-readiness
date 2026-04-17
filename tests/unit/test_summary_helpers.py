"""Unit tests for ``scripts/slz_readiness/_summary.py`` primitives."""
from __future__ import annotations

from slz_readiness import _summary


def test_severity_tally_fills_canonical_keys():
    gaps = [
        {"severity": "high"},
        {"severity": "high"},
        {"severity": "critical"},
        {"severity": "novel"},
    ]
    out = _summary.severity_tally(gaps)
    for key in _summary.SEVERITY_ORDER:
        assert key in out
    assert out["critical"] == 1
    assert out["high"] == 2
    assert out["novel"] == 1
    # Canonical keys appear before extras, in canonical order.
    keys = list(out)
    assert keys[: len(_summary.SEVERITY_ORDER)] == list(_summary.SEVERITY_ORDER)


def test_unknown_gaps_sorted_stably():
    gaps = [
        {"rule_id": "b", "resource_id": "x", "status": "unknown"},
        {"rule_id": "a", "resource_id": "x", "status": "unknown"},
        {"rule_id": "a", "resource_id": "w", "status": "missing"},
        {"rule_id": "a", "resource_id": "w", "status": "unknown"},
    ]
    out = _summary.unknown_gaps(gaps)
    assert [(g["rule_id"], g["resource_id"]) for g in out] == [
        ("a", "w"),
        ("a", "x"),
        ("b", "x"),
    ]


def test_render_table_pipe_aligned():
    out = _summary.render_table(["A", "BB"], [["1", "22"], ["333", "4"]])
    lines = out.split("\n")
    assert lines[0] == "| A   | BB |"
    assert lines[1] == "| --- | -- |"
    assert lines[2] == "| 1   | 22 |"
    # short rows get padded
    short = _summary.render_table(["A", "B"], [["1"]])
    assert short.split("\n")[-1].count("|") == 3


def test_header_block_has_metadata():
    block = _summary.header_block(
        "SLZ Plan summary",
        tenant="T",
        run_id="R",
        ts="2026-01-01T00:00:00.000Z",
    )
    assert block.startswith("# SLZ Plan summary")
    assert "tenant=T" in block
    assert "run=R" in block
    assert "ts=2026-01-01T00:00:00.000Z" in block


def test_write_json_sorted_and_newline(tmp_path):
    p = tmp_path / "x.json"
    _summary.write_json(p, {"b": 1, "a": 2})
    text = p.read_text(encoding="utf-8")
    assert text.endswith("\n")
    # Sorted keys -> "a" before "b".
    assert text.index('"a"') < text.index('"b"')


def test_error_findings_filter_and_sort():
    findings = [
        {"resource_id": "B", "resource_type": "mg", "observed_state": {"error": "x"}},
        {"resource_id": "A", "resource_type": "mg", "observed_state": {"name": "ok"}},
        {"resource_id": "A", "resource_type": "la", "observed_state": {"error": "y"}},
    ]
    out = _summary.error_findings(findings)
    assert [(f["resource_type"], f["resource_id"]) for f in out] == [("la", "A"), ("mg", "B")]
