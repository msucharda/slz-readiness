"""Heuristic MG-name → canonical-role proposer for brownfield reconcile.

The human-in-the-loop LLM prompt (``.github/prompts/slz-reconcile.prompt.md``)
emits an ``mg_alias.proposal.json`` file that ``slz-reconcile`` then
validates and writes. Before v0.8.0 the LLM was the ONLY path to a
proposal — which made the flow untestable and slow for obvious cases.

This module adds a deterministic heuristic: given a tenant's observed
MGs, guess the canonical role each belongs to using substring matching
on both ``id`` and ``displayName``. The output is a valid proposal
(``{role: str | None}``); roles the heuristic is unsure about are
emitted as ``None`` so the LLM still runs for the ambiguous cases.

The heuristic exists because MOST tenants use names that are close to
canonical (e.g. ``corp-mg``, ``Management``, ``lz-online``). For those
cases the LLM is overkill. The rule YAMLs and schema validator do all
the hard work; the proposer just saves a round trip.

Pure function. No LLM. No Azure. Unit-tested.
"""
from __future__ import annotations

import warnings
from collections.abc import Iterable
from typing import Any

from . import CANONICAL_ROLES

# Ordered matchers for each canonical role. The FIRST substring match
# wins when multiple roles could plausibly claim an MG — so more-specific
# patterns (``confidential_corp``) go before less-specific ones (``corp``).
# All matching is case-insensitive and works over both the MG's ``id``
# (the resource-id tail) and the ``displayName``.
#
# Ordering matters: confidential_corp / confidential_online MUST appear
# before corp / online; landingzones before zones; etc.
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
    claimed: Iterable[str] = (),
) -> str | None:
    """Return the first canonical role whose pattern matches ``mg_id``
    or ``display_name`` (case-insensitive, substring). Skips roles that
    are already claimed by a prior MG in the same proposal — first
    match wins.
    """
    haystack = f"{mg_id} {display_name}".lower()
    claimed_set = set(claimed)
    for role, patterns in _MATCH_PATTERNS:
        if role in claimed_set:
            continue
        for pat in patterns:
            if pat in haystack:
                return role
    return None


def build_heuristic_proposal(
    findings: Any,
) -> dict[str, str | None]:
    """Build a best-effort ``mg_alias.proposal.json`` payload.

    Returns a dict with EXACTLY the 14 canonical SLZ roles as keys. For
    each role the value is either:

    * a tenant MG ``id`` the heuristic confidently matched, or
    * ``None`` when no MG matched (the LLM pass should resolve these).

    ``findings`` is a parsed ``findings.json`` payload. No network, no
    LLM — pure function over the observed MG set.
    """
    proposal: dict[str, str | None] = {role: None for role in CANONICAL_ROLES}
    records = _extract_mg_records(findings)
    claimed_values: set[str] = set()

    for rec in records:
        mg_id = rec.get("id", "")
        display = rec.get("displayName", "")
        claimed_roles = [r for r, v in proposal.items() if v is not None]
        role = _match_role(mg_id, display, claimed=claimed_roles)
        if role is None:
            continue
        if proposal.get(role) is not None:
            continue  # already claimed; keep first match
        if mg_id in claimed_values:
            continue  # this MG already mapped to some other role
        proposal[role] = mg_id
        claimed_values.add(mg_id)

    return proposal
