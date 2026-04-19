"""Tests for v0.8.0 reconcile heuristic proposer."""
from __future__ import annotations

import warnings

from slz_readiness.reconcile import CANONICAL_ROLES
from slz_readiness.reconcile.proposer import build_heuristic_proposal


def _mg_summary(mgs: list[dict[str, str]]) -> dict:
    """Build a findings.json payload with a single MG-summary finding.

    ``present_details`` is emitted as a **list** of ``{id, displayName,
    parent_id}`` records — the canonical shape produced by
    ``discover/mg_hierarchy.py``.
    """
    return {
        "findings": [
            {
                "resource_type": "microsoft.management/managementgroups.summary",
                "resource_id": "tenant",
                "scope": "tenant",
                "observed_state": {
                    "present_ids": [m["id"] for m in mgs],
                    "present_details": [
                        {
                            "id": m["id"],
                            "displayName": m["displayName"],
                            "parent_id": m.get("parent_id"),
                        }
                        for m in mgs
                    ],
                },
                "query_cmd": "az account management-group list",
            }
        ]
    }


def _mg_summary_legacy_dict(mgs: list[dict[str, str]]) -> dict:
    """Legacy shape: ``present_details`` as a dict keyed by MG id."""
    return {
        "findings": [
            {
                "resource_type": "microsoft.management/managementgroups.summary",
                "resource_id": "tenant",
                "scope": "tenant",
                "observed_state": {
                    "present_ids": [m["id"] for m in mgs],
                    "present_details": {
                        m["id"]: {"displayName": m["displayName"]} for m in mgs
                    },
                },
                "query_cmd": "az account management-group list",
            }
        ]
    }


def test_heuristic_accepts_legacy_dict_shape() -> None:
    """Back-compat: older fixtures may still emit ``present_details`` as a
    dict keyed by MG id. Proposer must handle that shape (with a
    DeprecationWarning) and still match on displayName.
    """
    findings = _mg_summary_legacy_dict([
        {"id": "mg-a", "displayName": "Management"},
        {"id": "mg-b", "displayName": "Sandbox"},
    ])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = build_heuristic_proposal(findings)
    assert result["management"] == "mg-a"
    assert result["sandbox"] == "mg-b"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


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


def test_heuristic_one_role_one_mg_ties_emit_null() -> None:
    """Two MGs with identical score for ``corp`` → null (LLM resolves).

    v0.10.0 replaced first-match-wins with top-1 selection and
    null-on-tie. Here both ``corp-production`` and ``corp-staging``
    score exactly +1 (substring match only), so neither claims.
    """
    findings = _mg_summary([
        {"id": "corp-production", "displayName": "Corp Prod"},
        {"id": "corp-staging", "displayName": "Corp Stage"},
    ])
    result = build_heuristic_proposal(findings)
    assert result["corp"] is None


def test_heuristic_no_mg_summary_returns_all_null() -> None:
    """Findings.json without an MG-summary finding → all-null proposal.
    Same behaviour as empty findings: the LLM must resolve everything."""
    findings = {"findings": [
        {"resource_type": "something-else", "observed_state": {}},
    ]}
    result = build_heuristic_proposal(findings)
    assert all(v is None for v in result.values())


def test_heuristic_full_canonical_names() -> None:
    """Tenant that deploys SLZ canonically — heuristic maps every role.

    All MGs carry an explicit (fake) parent_id so none of them are hit
    by the ``slz``-role tenant-root hard-filter; this lets ``slz`` claim
    its own MG first and frees downstream roles (notably
    ``landingzones``) from the ``"lz" ⊂ "slz"`` substring false-positive.
    """
    mgs = [
        {
            "id": role,
            "displayName": role.replace("_", " ").title(),
            "parent_id": "fake-parent",
        }
        for role in CANONICAL_ROLES
    ]
    findings = _mg_summary(mgs)
    result = build_heuristic_proposal(findings)
    # At minimum, these obvious ones must round-trip:
    for role in (
        "corp",
        "online",
        "platform",
        "management",
        "sandbox",
        "identity",
        "landingzones",
        "slz",
    ):
        assert result[role] == role, f"role {role} did not round-trip: got {result[role]}"


def test_heuristic_prefers_intermediate_over_tenant_root() -> None:
    """Structural (v0.10.0): ``slz`` role must pick the MG whose children
    look like SLZ intermediates, never the tenant root.

    Mirrors the real slz-demo shape:
    ``tenant-root -> sucharda -> alz -> {platform, workloads}``.
    The old first-match-wins heuristic picked ``tenant-root`` (substring
    ``root``); structural scoring filters root out and picks ``alz``
    (the MG whose two children are ``platform`` and ``workloads``).
    """
    findings = _mg_summary([
        {"id": "tenant-root", "displayName": "Tenant Root Group", "parent_id": None},
        {"id": "sucharda", "displayName": "sucharda", "parent_id": "tenant-root"},
        {"id": "alz", "displayName": "Sovereign Landing Zone", "parent_id": "sucharda"},
        {"id": "platform", "displayName": "platform", "parent_id": "alz"},
        {"id": "workloads", "displayName": "workloads", "parent_id": "alz"},
    ])
    result = build_heuristic_proposal(findings)
    assert result["slz"] == "alz"
    assert result["platform"] == "platform"
    # ``workloads`` has no substring match for any landingzones pattern
    # and ``alz`` is already claimed by ``slz`` → the role stays null
    # and the LLM resolves it.
    assert result["landingzones"] is None


def test_heuristic_parent_signal_for_platform() -> None:
    """Structural (v0.10.0): role ``platform`` gets +2 when its parent
    MG is claimed as ``slz``. Makes platform robust even against
    ambiguous sibling names.
    """
    findings = _mg_summary([
        {"id": "root", "displayName": "Tenant Root Group", "parent_id": None},
        {"id": "slz-mg", "displayName": "Sovereign Landing Zone", "parent_id": "root"},
        {"id": "platform-plat", "displayName": "Platform", "parent_id": "slz-mg"},
        {"id": "landingzones-lz", "displayName": "Landing Zones", "parent_id": "slz-mg"},
    ])
    result = build_heuristic_proposal(findings)
    # slz_mg candidate has >=2 SLZ-shape children → +3; substring +1 → 4.
    assert result["slz"] == "slz-mg"
    # platform-plat: substring +1, parent is slz-mg → +2 = 3.
    assert result["platform"] == "platform-plat"
    assert result["landingzones"] == "landingzones-lz"


def test_heuristic_slz_tenant_root_is_excluded() -> None:
    """Structural (v0.10.0): the MG with ``parent_id is None`` — the
    tenant root — is never eligible for role ``slz`` even when its
    name substring-matches ``slz`` / ``sovereign`` / ``root``.
    """
    findings = _mg_summary([
        {
            "id": "root",
            "displayName": "Sovereign Root",
            "parent_id": None,
        },
        {
            "id": "alz",
            "displayName": "Sovereign Landing Zone",
            "parent_id": "root",
        },
        {"id": "platform", "displayName": "platform", "parent_id": "alz"},
        {"id": "landingzones", "displayName": "Landing Zones", "parent_id": "alz"},
    ])
    result = build_heuristic_proposal(findings)
    assert result["slz"] == "alz"
