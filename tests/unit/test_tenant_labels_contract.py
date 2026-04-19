"""Prompt-contract test: tenant enumeration surfaces display-name labels.

The discover slash-command and skill must instruct the agent to call
`az account tenant list` in addition to `az account list`, to fall back
to Microsoft Graph's `findTenantInformationByTenantId` when CLI
enrichment yields nulls (a real regression observed against tenants the
caller is a guest in), and finally to compose a clearly-labelled
subscription-name hint when even Graph yields nothing. The enum
*values* must remain raw `tenantId` GUIDs for parsing robustness.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROMPT = REPO / ".github" / "prompts" / "slz-discover.prompt.md"
SKILL = REPO / ".github" / "skills" / "discover" / "SKILL.md"


def _shared_assertions(text: str) -> None:
    # Both CLI enrichment calls.
    assert "az account list" in text
    assert "az account tenant list" in text
    assert "defaultDomain" in text
    # Composite label recipe is present.
    assert "(<defaultDomain>)" in text
    # Values are still raw GUIDs.
    assert "raw" in text and "tenantId" in text and "GUIDs" in text
    # Graph fallback for tenants where CLI yields nothing usable.
    assert "findTenantInformationByTenantId" in text
    assert "graph.microsoft.com" in text
    assert "az rest" in text and "--method GET" in text
    # Final-tier subscription-name hint, with the mandatory `(e.g. ...)`
    # marker so it is never mistaken for a synthesised tenant name.
    assert "(e.g. <sub1>, <sub2>)" in text


def test_prompt_enriches_tenant_labels() -> None:
    text = PROMPT.read_text(encoding="utf-8")
    _shared_assertions(text)
    assert "displayName:displayName" in text
    # Legacy "Do NOT synthesise a display name" forbid-sentence is gone.
    assert "Do NOT synthesise a display name" not in text


def test_skill_enriches_tenant_labels() -> None:
    text = SKILL.read_text(encoding="utf-8")
    _shared_assertions(text)
