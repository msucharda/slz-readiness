"""Heuristic MG-name → canonical-role proposer for brownfield reconcile.

The human-in-the-loop LLM prompt (``.github/prompts/slz-reconcile.prompt.md``)
emits an ``mg_alias.proposal.json`` file that ``slz-reconcile`` then
validates and writes. Before v0.8.0 the LLM was the ONLY path to a
proposal — which made the flow untestable and slow for obvious cases.

This module adds a deterministic heuristic: given a tenant's observed
MGs, guess the canonical role each belongs to by combining substring
matching on ``id`` / ``displayName`` with **structural signals** drawn
from the MG tree (``parent_id`` + children shape). Each candidate is
scored; the top-scoring candidate wins. Ties emit ``None`` so the LLM
resolves ambiguity downstream.

**Scoring (v0.10.0):**

* Substring match on ``id`` or ``displayName`` → ``+1``.
* Role ``slz`` excludes the tenant root (``parent_id is None``) outright.
* Role ``slz`` gains ``+3`` when the candidate has ≥2 children whose
  names look like SLZ intermediate children (``platform``, ``landing*``,
  ``workload*``, ``management``, ``connectivity``, ``identity``,
  ``security``, ``sandbox``, ``decomm*``).
* Roles ``platform`` / ``landingzones`` / ``sandbox`` / ``decommissioned``
  gain ``+2`` when their candidate's parent is the MG already claimed
  by ``slz``.

Selection: for each role iterate unclaimed MGs, compute scores, keep
the unique top scorer (``score > 0``); ties become ``None``. Roles are
processed with ``slz`` first so downstream parent-signals can reference
it, then in ``_MATCH_PATTERNS`` order (so ``confidential_corp`` claims
before ``corp``, etc.).

The rule YAMLs and schema validator do all the hard work; the proposer
just saves a round trip for the easy cases and emits ``None`` for the
long tail. Pure function. No LLM. No Azure. Unit-tested.
"""
from __future__ import annotations

import warnings
from typing import Any

from . import CANONICAL_ROLES

# Ordered matchers for each canonical role. Iteration order drives
# precedence when two roles would otherwise be eligible for the same MG
# — more-specific patterns (``confidential_corp``) MUST come before the
# less-specific ones (``corp``), landingzones before the bare ``lz``
# substring collides, etc. Matching is case-insensitive and runs over
# both the MG's ``id`` (resource-id tail) and its ``displayName``.
_MATCH_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("confidential_corp", ("confidential_corp", "confidential-corp", "conf-corp", "confcorp")),
    (
        "confidential_online",
        ("confidential_online", "confidential-online", "conf-online", "confonline"),
    ),
    ("landingzones", ("landingzones", "landing-zones", "landingzone", "landing_zones", "lz")),
    ("decommissioned", ("decommissioned", "decomm", "retired")),
    ("connectivity", ("connectivity", "conn", "network", "hub")),
    ("management", ("management", "mgmt", "mgmnt")),
    ("identity", ("identity", "idam", "ident")),
    ("security", ("security", "sec")),
    ("sandbox", ("sandbox", "sbox", "dev-sandbox")),
    ("platform", ("platform", "plat")),
    ("corp", ("corp", "corporate", "private")),
    ("online", ("online", "pub-facing", "external")),
    ("public", ("public", "pub")),
    ("slz", ("slz", "sovereign", "root")),
)

_PATTERNS_BY_ROLE: dict[str, tuple[str, ...]] = dict(_MATCH_PATTERNS)

# Keyword set used to recognise a child MG that "looks like" an SLZ
# intermediate's direct child (platform / landing-zones / management /
# etc.). Intentionally broader than ``_MATCH_PATTERNS`` — this is a
# shape signal, not an identity match, so common variants (``workload``
# for landing-zones-style children, ``decomm`` / ``sandbox``) are
# included. Kept alphabetised within each conceptual group.
_SLZ_INTERMEDIATE_CHILD_KEYWORDS: tuple[str, ...] = (
    "connectivity",
    "decomm",
    "hub",
    "idam",
    "identity",
    "landing",
    "landing-zones",
    "landing_zones",
    "landingzones",
    "lz",
    "management",
    "mgmt",
    "network",
    "plat",
    "platform",
    "retired",
    "sandbox",
    "sbox",
    "security",
    "workload",
)

# Roles whose natural parent is the SLZ intermediate MG — they pick up a
# +2 bump when the candidate's parent is already claimed as ``slz``.
_PARENT_SIGNAL_ROLES: frozenset[str] = frozenset(
    {"platform", "landingzones", "sandbox", "decommissioned"}
)


def _normalise_present_details(raw: Any) -> dict[str, dict[str, Any]]:
    """Return a ``{id: {displayName, parent_id, ...}}`` lookup.

    Accepts both the canonical list shape emitted by
    ``discover/mg_hierarchy.py`` (list of ``{id, displayName, parent_id}``
    records) and the deprecated dict shape (``{id: {displayName}}``) that
    older fixtures may still carry. Unknown shapes → empty dict.

    The dict shape triggers a ``DeprecationWarning``; it is scheduled for
    removal in a future minor once all baselines are regenerated.
    """
    if isinstance(raw, list):
        out: dict[str, dict[str, Any]] = {}
        for rec in raw:
            if not isinstance(rec, dict):
                continue
            mg_id = rec.get("id")
            if not isinstance(mg_id, str):
                continue
            out[mg_id] = rec
        return out
    if isinstance(raw, dict):
        warnings.warn(
            "reconcile.proposer: 'present_details' dict shape is deprecated; "
            "emit a list of {id, displayName, parent_id} records instead.",
            DeprecationWarning,
            stacklevel=3,
        )
        return {
            str(mg_id): rec
            for mg_id, rec in raw.items()
            if isinstance(rec, dict)
        }
    return {}


def _extract_mg_records(findings: Any) -> list[dict[str, Any]]:
    """Pull out ``{id, displayName, parent_id}`` tuples for every MG the
    tenant has.

    Looks inside ``findings.json`` for the MG-summary finding written by
    ``discover/mg_hierarchy.py``. Returns ``[]`` when the summary is
    absent — callers then propose an all-null alias (same as greenfield).
    """
    records = findings.get("findings") if isinstance(findings, dict) else findings
    if not isinstance(records, list):
        return []
    out: list[dict[str, Any]] = []
    for f in records:
        if not isinstance(f, dict):
            continue
        if f.get("resource_type") != "microsoft.management/managementgroups.summary":
            continue
        obs = f.get("observed_state") or {}
        ids = obs.get("present_ids") or []
        details_by_id = _normalise_present_details(obs.get("present_details"))
        for mg_id in ids:
            key = str(mg_id)
            info = details_by_id.get(key) or {}
            rec: dict[str, Any] = {
                "id": key,
                "displayName": str(info.get("displayName", key)),
                "parent_id": info.get("parent_id"),
            }
            out.append(rec)
        break  # one summary finding is authoritative
    return out


def _match_role(
    mg_id: str,
    display_name: str,
) -> str | None:
    """Return the first canonical role whose pattern matches ``mg_id``
    or ``display_name`` (case-insensitive, substring).

    Retained for external callers / tests that only need the raw
    substring classification without structural scoring; the proposer
    itself no longer uses this function.
    """
    haystack = f"{mg_id} {display_name}".lower()
    for role, patterns in _MATCH_PATTERNS:
        for pat in patterns:
            if pat in haystack:
                return role
    return None


def _score_candidate(
    role: str,
    mg: dict[str, Any],
    *,
    children_by_parent: dict[str, list[dict[str, Any]]],
    slz_mg_id: str | None,
) -> int:
    """Score one MG against one canonical role.

    Returns a non-negative integer score, or ``-1`` when a hard filter
    (currently only "tenant root excluded from role ``slz``") disqualifies
    the candidate outright. A score of ``0`` just means "no signals
    fired" and is treated as no-match by the caller.
    """
    mg_id = str(mg.get("id", ""))
    display = str(mg.get("displayName", "") or "")
    parent_id = mg.get("parent_id")
    haystack = f"{mg_id} {display}".lower()

    patterns = _PATTERNS_BY_ROLE.get(role, ())
    has_substring_match = any(pat in haystack for pat in patterns)
    score = 1 if has_substring_match else 0

    if role == "slz":
        # Tenant root is never the SLZ intermediate MG. Customer tenants
        # that deploy SLZ create a dedicated intermediate somewhere below
        # the root; an agent that maps ``slz`` to the tenant root is
        # always wrong in practice.
        if parent_id is None:
            return -1
        children = children_by_parent.get(mg_id, [])
        shape_hits = 0
        for child in children:
            child_haystack = (
                f"{child.get('id', '')} {child.get('displayName', '') or ''}".lower()
            )
            if any(kw in child_haystack for kw in _SLZ_INTERMEDIATE_CHILD_KEYWORDS):
                shape_hits += 1
        if shape_hits >= 2:
            score += 3

    # Parent-signal is a TIEBREAKER, not a standalone claim — an MG
    # whose name carries no hint of the role should never be claimed
    # just because its parent is the SLZ intermediate. Only reinforce
    # an existing substring hit.
    if (
        role in _PARENT_SIGNAL_ROLES
        and has_substring_match
        and slz_mg_id is not None
        and parent_id == slz_mg_id
    ):
        score += 2

    return score


def build_heuristic_proposal(
    findings: Any,
) -> dict[str, str | None]:
    """Build a best-effort ``mg_alias.proposal.json`` payload.

    Returns a dict with EXACTLY the 14 canonical SLZ roles as keys. For
    each role the value is either:

    * a tenant MG ``id`` the heuristic confidently picked, or
    * ``None`` when no candidate scored above zero **or** when the top
      score was tied — the LLM pass resolves those.

    ``findings`` is a parsed ``findings.json`` payload. No network, no
    LLM — pure function over the observed MG set + parent-chain.
    """
    proposal: dict[str, str | None] = {role: None for role in CANONICAL_ROLES}
    records = _extract_mg_records(findings)
    if not records:
        return proposal

    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        parent = rec.get("parent_id")
        if isinstance(parent, str) and parent:
            children_by_parent.setdefault(parent, []).append(rec)

    # Resolve ``slz`` first so downstream roles (platform / landingzones
    # / sandbox / decommissioned) can use the parent-is-slz bump. Then
    # walk the rest of _MATCH_PATTERNS in its declared order so more-
    # specific patterns (confidential_corp) still claim before the less
    # specific ones (corp).
    role_order: list[str] = ["slz"] + [r for r, _ in _MATCH_PATTERNS if r != "slz"]
    claimed_values: set[str] = set()

    for role in role_order:
        best_score = 0
        best_mgs: list[str] = []
        for rec in records:
            mg_id = str(rec.get("id", ""))
            if not mg_id or mg_id in claimed_values:
                continue
            score = _score_candidate(
                role,
                rec,
                children_by_parent=children_by_parent,
                slz_mg_id=proposal.get("slz"),
            )
            if score <= 0:
                continue
            if score > best_score:
                best_score = score
                best_mgs = [mg_id]
            elif score == best_score:
                best_mgs.append(mg_id)
        if len(best_mgs) == 1:
            winner = best_mgs[0]
            proposal[role] = winner
            claimed_values.add(winner)
        # else: no match (best_mgs empty) or tie (>1 entry) → null; the
        # LLM per-role ask_user loop resolves it.

    return proposal
