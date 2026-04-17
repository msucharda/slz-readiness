"""Golden tests for the deterministic evaluate engine.

Two fixtures are run through evaluate() and their stable, sorted gap rule_ids
compared against the expected set. Deterministic — no network, no LLM.
"""
from __future__ import annotations

import json
from pathlib import Path

from slz_readiness.evaluate.engine import evaluate, gap_to_dict

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load(name: str) -> list:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data["findings"]


def test_minimal_non_compliant_produces_expected_gaps() -> None:
    findings = _load("minimal_non_compliant.findings.json")
    gaps = evaluate(findings)
    rule_ids = sorted({g.rule_id for g in gaps})
    assert rule_ids == [
        "identity.platform_identity_mg_exists",
        "logging.management_mg_exists",
        "mg.slz.hierarchy_shape",
        "policy.slz.sovereign_root_policies_applied",
        "sovereignty.confidential_corp_policies_applied",
        "sovereignty.confidential_online_policies_applied",
    ]


def test_mostly_compliant_only_flags_confidential_archetypes() -> None:
    findings = _load("mostly_compliant.findings.json")
    gaps = evaluate(findings)
    rule_ids = sorted({g.rule_id for g in gaps})
    assert rule_ids == [
        "sovereignty.confidential_corp_policies_applied",
        "sovereignty.confidential_online_policies_applied",
    ]


def test_evaluate_is_deterministic() -> None:
    findings = _load("minimal_non_compliant.findings.json")
    a = [gap_to_dict(g) for g in evaluate(findings)]
    b = [gap_to_dict(g) for g in evaluate(findings)]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
