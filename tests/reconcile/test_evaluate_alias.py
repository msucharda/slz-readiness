"""Brownfield alias tests: Evaluate rewrites scopes when mg_alias.json maps a role."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from slz_readiness.evaluate.engine import evaluate
from slz_readiness.evaluate.engine import run as evaluate_run


def _mg_summary_finding(present: list[str]) -> dict[str, Any]:
    return {
        "resource_type": "microsoft.management/managementgroups.summary",
        "resource_id": "tenant",
        "observed_state": {"present_ids": present},
    }


def test_hierarchy_shape_passes_when_aliased() -> None:
    """With alias {corp: prod-int}, the hierarchy-shape rule accepts
    `prod-int` as the `corp` entry. All other canonical names must
    still be present for the rule to pass — we substitute only what's
    aliased, and leave everything else canonical."""
    # Tenant has the canonical SLZ MGs EXCEPT 'corp' — instead it has 'prod-int'.
    canonical_except_corp = [
        "slz", "platform", "landingzones", "public", "online",
        "confidential_corp", "confidential_online", "sandbox", "security",
        "management", "connectivity", "identity", "decommissioned",
    ]
    present = [*canonical_except_corp, "prod-int"]
    findings = [_mg_summary_finding(present)]

    # Without alias → fails (corp missing).
    gaps_no_alias = evaluate(findings)
    shape_gaps = [g for g in gaps_no_alias if g.rule_id == "mg.slz.hierarchy_shape"]
    assert shape_gaps, "expected hierarchy_shape gap without alias"
    assert "corp" in shape_gaps[0].observed.get("missing", [])

    # With alias {corp: prod-int} → passes.
    gaps_with_alias = evaluate(findings, alias_map={"corp": "prod-int"})
    shape_gaps = [g for g in gaps_with_alias if g.rule_id == "mg.slz.hierarchy_shape"]
    assert not shape_gaps, f"expected no hierarchy_shape gap; got {shape_gaps!r}"


def test_run_loads_alias_from_sibling_file(tmp_path: Path) -> None:
    """End-to-end through run(): mg_alias.json sibling of gaps.json is consumed."""
    canonical_except_corp = [
        "slz", "platform", "landingzones", "public", "online",
        "confidential_corp", "confidential_online", "sandbox", "security",
        "management", "connectivity", "identity", "decommissioned",
    ]
    present = [*canonical_except_corp, "prod-int"]

    findings_path = tmp_path / "findings.json"
    findings_path.write_text(
        json.dumps({"findings": [_mg_summary_finding(present)]}),
        encoding="utf-8",
    )
    alias_path = tmp_path / "mg_alias.json"
    alias_path.write_text(
        json.dumps({"corp": "prod-int"}, indent=2),
        encoding="utf-8",
    )
    gaps_path = tmp_path / "gaps.json"

    evaluate_run(findings_path, gaps_path)

    gaps = json.loads(gaps_path.read_text(encoding="utf-8"))["gaps"]
    shape_gaps = [g for g in gaps if g["rule_id"] == "mg.slz.hierarchy_shape"]
    assert not shape_gaps, "alias file should have suppressed the hierarchy gap"


def test_missing_alias_file_is_greenfield_safe(tmp_path: Path) -> None:
    """No mg_alias.json → Evaluate runs exactly as pre-v0.6.0."""
    findings_path = tmp_path / "findings.json"
    findings_path.write_text(
        json.dumps({"findings": [_mg_summary_finding([])]}), encoding="utf-8"
    )
    gaps_path = tmp_path / "gaps.json"

    evaluate_run(findings_path, gaps_path)

    gaps = json.loads(gaps_path.read_text(encoding="utf-8"))["gaps"]
    shape_gaps = [g for g in gaps if g["rule_id"] == "mg.slz.hierarchy_shape"]
    # All 14 canonical MGs missing; the rule fires.
    assert shape_gaps, "expected hierarchy_shape gap without alias file"


def test_malformed_alias_file_is_treated_as_empty(tmp_path: Path) -> None:
    """A garbage mg_alias.json must not break Evaluate — log and skip."""
    findings_path = tmp_path / "findings.json"
    findings_path.write_text(
        json.dumps({"findings": [_mg_summary_finding([])]}), encoding="utf-8"
    )
    (tmp_path / "mg_alias.json").write_text("not json {", encoding="utf-8")
    gaps_path = tmp_path / "gaps.json"

    # Must not raise.
    evaluate_run(findings_path, gaps_path)

    gaps = json.loads(gaps_path.read_text(encoding="utf-8"))["gaps"]
    shape_gaps = [g for g in gaps if g["rule_id"] == "mg.slz.hierarchy_shape"]
    assert shape_gaps, "malformed alias → should fall back to canonical shape"
