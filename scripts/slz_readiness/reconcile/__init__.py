"""slz-reconcile — brownfield bridge between Discover and Evaluate (v0.6.0 skeleton).

Produces ``artifacts/<run>/mg_alias.json`` mapping the 14 canonical SLZ
management-group roles to the operator's actual MG names (or ``null`` when
the role is absent / irrelevant). Evaluate reads this file to rewrite
``matcher.selector.scope: mg/<role>`` into the aliased name, so rules
evaluate against the tenant's real hierarchy.

Architectural invariant: this is the **only** LLM-writes-artifact phase.
The LLM proposes mappings inside the Copilot prompt surface; this CLI is
a deterministic, schema-gated **writer**. Evaluate keeps its zero-LLM
contract.
"""
from __future__ import annotations

# The 14 canonical SLZ roles, sourced from mg/slz_hierarchy_shape.yml:expected.
# Keep sorted; the schema and alias file ordering MUST stay byte-stable.
CANONICAL_ROLES: tuple[str, ...] = (
    "confidential_corp",
    "confidential_online",
    "connectivity",
    "corp",
    "decommissioned",
    "identity",
    "landingzones",
    "management",
    "online",
    "platform",
    "public",
    "sandbox",
    "security",
    "slz",
)
