"""Dataclasses for the phase contracts (Discover → Evaluate → Plan → Scaffold)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BaselineRef:
    """Pointer to a specific file at a specific SHA in the vendored baseline."""

    source: str  # e.g. "https://github.com/Azure/Azure-Landing-Zones-Library"
    path: str    # e.g. "platform/slz/archetype_definitions/sovereign_root.alz_archetype_definition.json"
    sha: str     # git blob sha


@dataclass
class Finding:
    """A single observed fact about the tenant, emitted by Discover."""

    resource_type: str
    resource_id: str
    scope: str
    observed_state: dict[str, Any]
    query_cmd: str


@dataclass
class Gap:
    """A single rule failure emitted by Evaluate."""

    rule_id: str
    severity: str  # "critical" | "high" | "medium" | "low" | "info"
    design_area: str  # "mg" | "identity" | "policy" | "logging" | "sovereignty"
    observed: Any
    expected: Any
    baseline_ref: BaselineRef
    resource_id: str
    message: str
    remediation_template: str | None = None  # maps to scripts/scaffold/avm_templates/<name>.bicep
    remediation_params: dict[str, Any] = field(default_factory=dict)
