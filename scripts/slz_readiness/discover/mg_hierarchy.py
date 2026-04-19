"""Discover Azure management-group hierarchy."""
from __future__ import annotations

from typing import Any

from .az_common import az_cmd_str, run_az


def _list_mgs() -> list[dict[str, Any]]:
    args = ["account", "management-group", "list", "--no-register"]
    try:
        mgs = run_az(args)
    except Exception:  # noqa: BLE001 - tolerate empty tenants / permission errors
        mgs = []
    return mgs or []


def _show_mg(name: str) -> dict[str, Any] | None:
    """Return ``az account management-group show --name <n> --expand`` output.

    Returns None on any error (permission, not-found, transient). The
    caller treats missing results as "parent unknown" — the docs-only
    brownfield-move guidance degrades gracefully.
    """
    args = ["account", "management-group", "show", "--name", name, "--expand", "--no-register"]
    try:
        return run_az(args)
    except Exception:  # noqa: BLE001
        return None


def present_mg_ids() -> list[str]:
    """Return the sorted list of management-group names present in the tenant.

    Callers (``policy_assignments``, ``identity_rbac``) intersect their
    hardcoded SLZ ``SCOPES`` with this list to avoid probing absent MGs.
    Empty list on error / empty tenants.
    """
    return sorted({name for mg in _list_mgs() if (name := mg.get("name"))})


def _collect_present_details(mgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ``[{id, displayName, parent_id}, ...]`` sorted by id.

    Uses one ``show`` per MG to resolve ``properties.details.parent.id``
    — the flat ``list`` output does not carry parent information.
    Staleness caveat: RG would be faster but can lag up to 15 min on MG
    moves; given Discover is read-only, the worst case is a stale
    suggestion in how-to-deploy.md, which the operator verifies manually.
    """
    details: list[dict[str, Any]] = []
    for mg in mgs:
        name = mg.get("name")
        if not name:
            continue
        display = mg.get("displayName") or name
        parent_id: str | None = None
        shown = _show_mg(name)
        if isinstance(shown, dict):
            parent = (shown.get("properties") or {}).get("details", {}).get("parent") or {}
            if isinstance(parent, dict):
                p_name = parent.get("name") or parent.get("id", "").rsplit("/", 1)[-1] or None
                if p_name:
                    parent_id = p_name
        details.append({"id": name, "displayName": display, "parent_id": parent_id})
    details.sort(key=lambda d: str(d.get("id", "")))
    return details


def discover() -> list[dict[str, Any]]:
    args = ["account", "management-group", "list", "--no-register"]
    try:
        mgs = run_az(args)
    except Exception:  # noqa: BLE001 - tolerate empty tenants / permission errors
        mgs = []
    mgs = mgs or []
    present_ids = sorted({mg.get("name") for mg in mgs if mg.get("name")})
    present_details = _collect_present_details(mgs)
    return [
        {
            "resource_type": "microsoft.management/managementgroups.summary",
            "resource_id": "tenant",
            "scope": "/",
            # ``present_ids`` kept for backwards-compat with existing
            # reconcile fixtures; ``present_details`` (v0.9.0+) carries
            # displayName + parent_id so reconcile proposer can label
            # enum options and scaffold how-to-deploy can emit
            # brownfield move-guidance. Fields are additive — consumers
            # MUST tolerate absent ``present_details``.
            "observed_state": {
                "present_ids": present_ids,
                "present_details": present_details,
            },
            "query_cmd": az_cmd_str(args),
        }
    ]

