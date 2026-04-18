"""Schema validator for ``mg_alias.json``.

Contract (every violation raises :class:`AliasSchemaError`):

1. Top-level is a dict with EXACTLY the 14 canonical SLZ role names as keys
   (see :data:`CANONICAL_ROLES`). Extra keys or missing keys both reject.
2. Each value is ``str`` or ``None``. No numbers, no nested dicts.
3. Non-null values are unique across roles — two roles cannot map to the
   same customer MG (would double-count policy assignments).
4. If a ``findings.json`` is supplied, every non-null value must appear in
   ``findings.present_ids`` — aliases pointing at non-existent MGs are
   rejected at write-time, not discovered at Evaluate-time.

The validator is pure and deterministic; it never touches Azure.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from . import CANONICAL_ROLES


class AliasSchemaError(ValueError):
    """Raised when ``mg_alias.json`` fails validation."""


def _collect_present_ids(findings: Any) -> set[str] | None:
    """Extract the tenant's observed MG set from a discover findings payload.

    Returns ``None`` when the payload does not contain an MG-summary
    finding — callers treat that as "skip the membership check" rather
    than a hard error, so a partial findings.json does not block
    greenfield runs.
    """
    records = findings.get("findings") if isinstance(findings, dict) else findings
    if not isinstance(records, list):
        return None
    for f in records:
        if not isinstance(f, dict):
            continue
        if f.get("resource_type") != "microsoft.management/managementgroups.summary":
            continue
        obs = f.get("observed_state")
        if isinstance(obs, dict):
            ids = obs.get("present_ids")
            if isinstance(ids, list):
                return {str(x) for x in ids}
    return None


def validate(
    alias: Any,
    findings: Any | None = None,
) -> dict[str, str | None]:
    """Validate ``alias`` and return a normalised dict suitable for writing.

    ``findings`` is optional. When provided (typically a parsed
    ``findings.json``), non-null values are cross-checked against
    ``present_ids``. When omitted, that check is skipped — useful for
    unit tests that don't care about tenant realism.
    """
    if not isinstance(alias, dict):
        raise AliasSchemaError(
            f"mg_alias.json top level must be a JSON object; got {type(alias).__name__}"
        )

    expected_keys = set(CANONICAL_ROLES)
    observed_keys = set(alias.keys())
    extra = observed_keys - expected_keys
    if extra:
        raise AliasSchemaError(f"unknown roles: {sorted(extra)}")

    normalised: dict[str, str | None] = {}
    for role in CANONICAL_ROLES:
        # v0.7.1: relaxed — missing roles default to ``null`` rather than
        # rejecting. Allows partial reconcile proposals (e.g. an LLM that
        # only knew how to map 8 of 14 roles); the empty roles fall back
        # to canonical-name matching at Evaluate-time.
        if role not in alias:
            normalised[role] = None
            continue
        value = alias[role]
        if value is None:
            normalised[role] = None
            continue
        if not isinstance(value, str):
            raise AliasSchemaError(
                f"role '{role}' must be str or null; got {type(value).__name__}"
            )
        stripped = value.strip()
        if not stripped:
            raise AliasSchemaError(f"role '{role}' has empty-string alias; use null instead")
        normalised[role] = stripped

    non_null = [v for v in normalised.values() if v is not None]
    dupes = [v for v, count in Counter(non_null).items() if count > 1]
    if dupes:
        raise AliasSchemaError(
            f"alias values must be unique across roles; duplicated: {sorted(dupes)}"
        )

    if findings is not None:
        present = _collect_present_ids(findings)
        if present is not None:
            unknown = [v for v in non_null if v not in present]
            if unknown:
                raise AliasSchemaError(
                    "alias values not present in tenant findings "
                    f"(no matching MG): {sorted(set(unknown))}"
                )

    return normalised


def empty_alias() -> dict[str, str | None]:
    """Return the canonical greenfield alias map: every role mapped to ``None``."""
    return {role: None for role in CANONICAL_ROLES}
