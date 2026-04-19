"""Prompt-contract test: tenant enumeration surfaces display-name labels.

The discover slash-command and skill must instruct the agent to call
`az account tenant list` in addition to `az account list`, and to compose
enum labels including `displayName` / `defaultDomain` when available. The
enum *values* must remain raw `tenantId` GUIDs for parsing robustness.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROMPT = REPO / ".github" / "prompts" / "slz-discover.prompt.md"
SKILL = REPO / ".github" / "skills" / "discover" / "SKILL.md"


def test_prompt_enriches_tenant_labels() -> None:
    text = PROMPT.read_text(encoding="utf-8")
    # Second enumeration call added.
    assert "az account tenant list" in text
    assert "displayName:displayName" in text
    assert "defaultDomain" in text
    # Composite label recipe is present.
    assert "(<defaultDomain>)" in text
    # Values are still raw GUIDs.
    assert "raw" in text and "tenantId" in text and "GUIDs" in text
    # Legacy "Do NOT synthesise a display name" forbid-sentence is gone.
    assert "Do NOT synthesise a display name" not in text


def test_skill_enriches_tenant_labels() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "az account tenant list" in text
    assert "defaultDomain" in text
    assert "(<defaultDomain>)" in text
    assert "raw" in text and "tenantId" in text and "GUIDs" in text
