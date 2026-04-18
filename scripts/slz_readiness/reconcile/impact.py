"""Python-rendered impact cards for Reconcile proposals.

The LLM in the `/slz-reconcile` prompt surface proposes role→MG mappings.
For each proposal we render a **bounded** markdown card — fixed layout,
no ANSI escapes, no LLM-controlled markdown injection — that the operator
sees inside the `ask_user` form before accepting or rejecting.

Four fields per card, from the research design:

* **Role** — the canonical SLZ role (e.g. ``corp``)
* **Proposal** — the customer MG name being proposed (``prod-int``)
* **Evidence** — 1-5 short bullet strings the LLM filled with signals
  it used (child subscription count, inherited policies, name similarity)
* **Impact** — 1-4 short bullet strings naming the rules that will
  re-evaluate if the operator accepts this mapping
"""
from __future__ import annotations

from dataclasses import dataclass

_MAX_EVIDENCE = 5
_MAX_IMPACT = 4


@dataclass(frozen=True)
class Proposal:
    role: str
    customer_mg: str
    evidence: list[str]
    impact: list[str]


def render(proposal: Proposal) -> str:
    """Return a bounded-markdown card suitable for an ``ask_user`` message field."""
    evidence = proposal.evidence[:_MAX_EVIDENCE]
    impact = proposal.impact[:_MAX_IMPACT]
    lines: list[str] = [
        f"**Role:** `{proposal.role}`",
        f"**Proposal:** map to `{proposal.customer_mg}`",
        "",
        "**Evidence:**",
    ]
    if evidence:
        lines.extend(f"- {line}" for line in evidence)
    else:
        lines.append("- _(no evidence supplied — decline unless obvious)_")
    lines.append("")
    lines.append("**Impact if accepted:**")
    if impact:
        lines.extend(f"- {line}" for line in impact)
    else:
        lines.append("- _(no rules indexed as affected by this role)_")
    return "\n".join(lines)
