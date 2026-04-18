"""Tests for v0.7.0 evaluate matcher rung-B equivalence —
``archetype_policies_applied`` falls back to ``policyDefinitionId`` when
an assignment was renamed by the operator."""
from __future__ import annotations

from typing import Any

from slz_readiness.evaluate import matchers
from slz_readiness.evaluate.models import BaselineRef


_DEFID_DENY_PIP = "/providers/Microsoft.Authorization/policyDefinitions/deny-pip"
_DEFID_AUDIT_STORAGE = "/providers/Microsoft.Authorization/policyDefinitions/audit-storage"
_DEFID_OTHER = "/providers/Microsoft.Authorization/policyDefinitions/something-else"

_ARCH_REF = {
    "source": "test",
    "path": "platform/alz/archetype_definitions/test_corp.alz_archetype_definition.json",
    "sha": "fakesha",
}


def _patch_baseline(monkeypatch, files: dict[str, Any]) -> None:
    """Replace ``read_baseline_json`` inside the matcher module so the
    matcher reads from an in-memory dict rather than the vendored tree.

    ``files`` is keyed by the ``BaselineRef.path`` field; ``KeyError``
    becomes the same exception ``read_baseline_json`` raises on a missing
    file, exercising the matcher's silent-fallback path.
    """
    def fake_read(ref: BaselineRef, manifest: Any | None = None) -> Any:
        if ref.path not in files:
            raise FileNotFoundError(ref.path)
        return files[ref.path]

    monkeypatch.setattr(matchers, "read_baseline_json", fake_read)


def test_defid_match_passes_when_name_renamed(monkeypatch) -> None:
    """Required name 'Deny-Public-IP' was deployed as 'CustomerDenyPublicIP'
    but with the same policyDefinitionId — the matcher should treat the
    rule as satisfied."""
    _patch_baseline(
        monkeypatch,
        {
            _ARCH_REF["path"]: {
                "policy_assignments": ["Deny-Public-IP", "Audit-Storage"]
            },
            "platform/alz/policy_assignments/Deny-Public-IP.alz_policy_assignment.json": {
                "properties": {"policyDefinitionId": _DEFID_DENY_PIP}
            },
            "platform/alz/policy_assignments/Audit-Storage.alz_policy_assignment.json": {
                "properties": {"policyDefinitionId": _DEFID_AUDIT_STORAGE}
            },
        },
    )

    observed = [
        {"name": "CustomerDenyPublicIP", "policyDefinitionId": _DEFID_DENY_PIP},
        {"name": "Audit-Storage", "policyDefinitionId": _DEFID_AUDIT_STORAGE},
    ]
    spec = {"archetype_ref": _ARCH_REF}

    passed, snap = matchers.archetype_policies_applied(observed, None, spec)

    assert passed is True
    assert snap["missing"] == []
    assert snap["present"] == ["Audit-Storage"]
    assert len(snap["matched_by_defid"]) == 1
    match = snap["matched_by_defid"][0]
    assert match["required_name"] == "Deny-Public-IP"
    assert match["observed_name"] == "CustomerDenyPublicIP"


def test_unknown_defid_keeps_missing(monkeypatch) -> None:
    """Renamed assignment with a *different* policyDefinitionId stays
    missing — the matcher must not pass the rule."""
    _patch_baseline(
        monkeypatch,
        {
            _ARCH_REF["path"]: {"policy_assignments": ["Deny-Public-IP"]},
            "platform/alz/policy_assignments/Deny-Public-IP.alz_policy_assignment.json": {
                "properties": {"policyDefinitionId": _DEFID_DENY_PIP}
            },
        },
    )

    observed = [
        {"name": "OperatorCustomThing", "policyDefinitionId": _DEFID_OTHER}
    ]
    passed, snap = matchers.archetype_policies_applied(
        observed, None, {"archetype_ref": _ARCH_REF}
    )

    assert passed is False
    assert snap["missing"] == ["Deny-Public-IP"]
    assert snap["matched_by_defid"] == []


def test_greenfield_parity_when_names_match(monkeypatch) -> None:
    """All names match → no need to load any per-assignment file. The
    matcher must NOT spuriously fall through to the def-id helper, which
    would change behaviour vs v0.6.0 (and would leak a FileNotFoundError
    if per-assignment files weren't vendored)."""
    _patch_baseline(
        monkeypatch,
        {_ARCH_REF["path"]: {"policy_assignments": ["A", "B"]}},
    )

    observed = [{"name": "A"}, {"name": "B"}]
    passed, snap = matchers.archetype_policies_applied(
        observed, None, {"archetype_ref": _ARCH_REF}
    )

    assert passed is True
    assert snap["missing"] == []
    assert snap["present"] == ["A", "B"]
    assert snap["matched_by_defid"] == []
