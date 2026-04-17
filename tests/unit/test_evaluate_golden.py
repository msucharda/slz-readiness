"""Golden tests for the deterministic evaluate engine.

Two fixtures are run through evaluate() and their stable, sorted gap rule_ids
compared against the expected set. Deterministic — no network, no LLM.
"""
from __future__ import annotations

import json
from pathlib import Path

from slz_readiness.evaluate.engine import evaluate, gap_to_dict

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# Fired by both fixtures: per-MG archetype coverage rules have no matching
# findings (selector targets mg/corp, mg/platform, etc.) so observed is empty
# and every required assignment shows as missing. Also fires when the fixture
# has no LA workspace findings.
_UNCONDITIONAL_FIRES = [
    "archetype.alz_connectivity_policies_applied",
    "archetype.alz_corp_policies_applied",
    "archetype.alz_decommissioned_policies_applied",
    "archetype.alz_identity_policies_applied",
    "archetype.alz_landing_zones_policies_applied",
    "archetype.alz_platform_policies_applied",
    "archetype.alz_sandbox_policies_applied",
    "archetype.slz_public_policies_applied",
    "logging.management_la_workspace_exists",
]


def _load(name: str) -> list:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data["findings"]


def test_minimal_non_compliant_produces_expected_gaps() -> None:
    findings = _load("minimal_non_compliant.findings.json")
    gaps = evaluate(findings)
    rule_ids = sorted({g.rule_id for g in gaps})
    assert rule_ids == sorted([
        *_UNCONDITIONAL_FIRES,
        "identity.platform_identity_mg_exists",
        "logging.management_mg_exists",
        "mg.slz.hierarchy_shape",
        "policy.slz.sovereign_root_policies_applied",
        "sovereignty.confidential_corp_policies_applied",
        "sovereignty.confidential_online_policies_applied",
    ])


def test_mostly_compliant_only_flags_confidential_archetypes() -> None:
    findings = _load("mostly_compliant.findings.json")
    gaps = evaluate(findings)
    rule_ids = sorted({g.rule_id for g in gaps})
    assert rule_ids == sorted([
        *_UNCONDITIONAL_FIRES,
        "sovereignty.confidential_corp_policies_applied",
        "sovereignty.confidential_online_policies_applied",
    ])


def test_evaluate_is_deterministic() -> None:
    findings = _load("minimal_non_compliant.findings.json")
    a = [gap_to_dict(g) for g in evaluate(findings)]
    b = [gap_to_dict(g) for g in evaluate(findings)]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_all_gaps_carry_status_field() -> None:
    """v0.2.0 schema: every gap has status ∈ {missing, misconfigured, unknown}."""
    gaps = evaluate(_load("minimal_non_compliant.findings.json"))
    for g in gaps:
        assert g.status in {"missing", "misconfigured", "unknown"}, g


def test_error_findings_produce_unknown_severity_gaps() -> None:
    """Synthetic error finding must round-trip as severity=unknown, status=unknown."""
    findings = [
        {
            "resource_type": "microsoft.authorization/policyassignments",
            "resource_id": "scope:mg/slz",
            "scope": "mg/slz",
            "observed_state": {"error": "permission_denied"},
        }
    ]
    gaps = evaluate(findings)
    unknowns = [g for g in gaps if g.status == "unknown"]
    assert unknowns, "expected at least one unknown-severity gap"
    for g in unknowns:
        assert g.severity == "unknown"
        assert g.remediation_template is None
