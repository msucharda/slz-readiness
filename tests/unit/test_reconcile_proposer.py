"""Tests for v0.8.0 reconcile heuristic proposer."""
from __future__ import annotations

from slz_readiness.reconcile import CANONICAL_ROLES
from slz_readiness.reconcile.proposer import build_heuristic_proposal


def _mg_summary(mgs: list[dict[str, str]]) -> dict:
    """Build a findings.json payload with a single MG-summary finding."""
    return {
        "findings": [
            {
                "resource_type": "microsoft.management/managementgroups.summary",
                "resource_id": "tenant",
                "scope": "tenant",
                "observed_state": {
                    "present_ids": [m["id"] for m in mgs],
                    "present_details": {m["id"]: {"displayName": m["displayName"]} for m in mgs},
                },
                "query_cmd": "az account management-group list",
            }
        ]
    }


def test_heuristic_empty_findings_returns_all_null() -> None:
    result = build_heuristic_proposal({"findings": []})
    assert set(result.keys()) == set(CANONICAL_ROLES)
    assert all(v is None for v in result.values())


def test_heuristic_obvious_names() -> None:
    findings = _mg_summary([
        {"id": "corp-mg", "displayName": "Corp"},
        {"id": "Management", "displayName": "Management"},
        {"id": "Sandbox", "displayName": "Sandbox"},
    ])
    result = build_heuristic_proposal(findings)
    assert result["corp"] == "corp-mg"
    assert result["management"] == "Management"
    assert result["sandbox"] == "Sandbox"
    assert result["online"] is None
    assert result["slz"] is None


def test_heuristic_prefers_more_specific_match() -> None:
    """``confidential_corp`` must claim before ``corp`` does —
    the ordering in _MATCH_PATTERNS guarantees it."""
    findings = _mg_summary([
        {"id": "confcorp-mg", "displayName": "Confidential Corp"},
        {"id": "corp-mg", "displayName": "Corp"},
    ])
    result = build_heuristic_proposal(findings)
    assert result["confidential_corp"] == "confcorp-mg"
    assert result["corp"] == "corp-mg"


def test_heuristic_skips_unknown_mgs() -> None:
    """MGs whose names don't resemble any canonical role stay unmapped."""
    findings = _mg_summary([
        {"id": "acme-foo", "displayName": "Foo"},
        {"id": "acme-bar", "displayName": "Bar"},
    ])
    result = build_heuristic_proposal(findings)
    assert all(v is None for v in result.values())


def test_heuristic_one_role_one_mg() -> None:
    """Two MGs that both look like ``corp`` — only the first claims it."""
    findings = _mg_summary([
        {"id": "corp-production", "displayName": "Corp Prod"},
        {"id": "corp-staging", "displayName": "Corp Stage"},
    ])
    result = build_heuristic_proposal(findings)
    assert result["corp"] == "corp-production"
    # No second role claims the second MG; it stays orphaned.


def test_heuristic_no_mg_summary_returns_all_null() -> None:
    """Findings.json without an MG-summary finding → all-null proposal.
    Same behaviour as empty findings: the LLM must resolve everything."""
    findings = {"findings": [
        {"resource_type": "something-else", "observed_state": {}},
    ]}
    result = build_heuristic_proposal(findings)
    assert all(v is None for v in result.values())


def test_heuristic_full_canonical_names() -> None:
    """Tenant that deploys SLZ canonically — heuristic maps every role."""
    mgs = [
        {"id": role, "displayName": role.replace("_", " ").title()}
        for role in CANONICAL_ROLES
    ]
    findings = _mg_summary(mgs)
    result = build_heuristic_proposal(findings)
    # At minimum, these obvious ones must round-trip:
    for role in ("corp", "online", "platform", "management", "sandbox", "identity", "landingzones"):
        assert result[role] == role, f"role {role} did not round-trip: got {result[role]}"
