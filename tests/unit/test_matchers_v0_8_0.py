"""Tests for v0.8.0 rung-C/D matchers — ``policy_parameters_match`` and
``custom_initiative_equivalent``. Both surface drift as non-blocking
gap statuses (``parameter_drift`` / ``custom_initiative_drift``) rather
than ``missing``, so the engine's status threading is also exercised."""
from __future__ import annotations

from typing import Any

from slz_readiness.evaluate import matchers
from slz_readiness.evaluate.models import BaselineRef


_ARCH_REF = {
    "source": "test",
    "path": "platform/alz/archetype_definitions/test_corp.alz_archetype_definition.json",
    "sha": "fakesha",
}
_INIT_REF = {
    "source": "test",
    "path": "platform/slz/policy_set_definitions/Enforce_Sovereign_Global.alz_policy_set_definition.json",
    "sha": "fakesha",
}


def _patch_baseline(monkeypatch, files: dict[str, Any]) -> None:
    def fake_read(ref: BaselineRef, manifest: Any | None = None) -> Any:
        if ref.path not in files:
            raise FileNotFoundError(ref.path)
        return files[ref.path]

    monkeypatch.setattr(matchers, "read_baseline_json", fake_read)


# ---------------------------------------------------------------------------
# policy_parameters_match


def test_param_match_passes_when_values_equal(monkeypatch) -> None:
    _patch_baseline(
        monkeypatch,
        {
            _ARCH_REF["path"]: {"policy_assignments": ["Deny-Public-IP"]},
            "platform/alz/policy_assignments/Deny-Public-IP.alz_policy_assignment.json": {
                "properties": {
                    "parameters": {"effect": {"value": "Deny"}}
                }
            },
        },
    )
    observed = [{"name": "Deny-Public-IP", "parameters": {"effect": {"value": "Deny"}}}]
    passed, snapshot, status = matchers.policy_parameters_match(
        observed, None, {"archetype_ref": _ARCH_REF}
    )
    assert passed is True
    assert status is None
    assert snapshot == {"drifted_assignments": []}


def test_param_match_flags_drift_with_override_status(monkeypatch) -> None:
    _patch_baseline(
        monkeypatch,
        {
            _ARCH_REF["path"]: {"policy_assignments": ["Some-Assignment"]},
            "platform/alz/policy_assignments/Some-Assignment.alz_policy_assignment.json": {
                "properties": {
                    "parameters": {
                        "maxAllowedSize": {"value": 100},
                        "effect": {"value": "Audit"},  # default-ignored key
                    }
                }
            },
        },
    )
    observed = [
        {
            "name": "Some-Assignment",
            "parameters": {
                "maxAllowedSize": {"value": 999},
                "effect": {"value": "Disabled"},
            },
        }
    ]
    passed, snapshot, status = matchers.policy_parameters_match(
        observed, None, {"archetype_ref": _ARCH_REF}
    )
    assert passed is False
    assert status == "parameter_drift"
    assert "Some-Assignment" in snapshot["drifted_assignments"]
    drift = snapshot["drifted_assignments"]["Some-Assignment"]
    assert "maxAllowedSize" in drift
    assert drift["maxAllowedSize"] == {"observed": 999, "expected": 100}
    # ``effect`` is in the default ignore list → not surfaced
    assert "effect" not in drift


def test_param_match_respects_explicit_ignore_list(monkeypatch) -> None:
    _patch_baseline(
        monkeypatch,
        {
            _ARCH_REF["path"]: {"policy_assignments": ["A"]},
            "platform/alz/policy_assignments/A.alz_policy_assignment.json": {
                "properties": {"parameters": {"k1": {"value": 1}, "k2": {"value": 2}}}
            },
        },
    )
    observed = [{"name": "A", "parameters": {"k1": {"value": 99}, "k2": {"value": 99}}}]
    passed, _snap, _s = matchers.policy_parameters_match(
        observed,
        None,
        {"archetype_ref": _ARCH_REF, "ignore_parameters": ["k1", "k2"]},
    )
    assert passed is True


def test_param_match_skips_absent_assignment(monkeypatch) -> None:
    """Assignment not present on tenant — that's rung-B's job, not ours."""
    _patch_baseline(
        monkeypatch,
        {
            _ARCH_REF["path"]: {"policy_assignments": ["Missing-One"]},
            "platform/alz/policy_assignments/Missing-One.alz_policy_assignment.json": {
                "properties": {"parameters": {"x": {"value": 1}}}
            },
        },
    )
    passed, _snap, _s = matchers.policy_parameters_match(
        [], None, {"archetype_ref": _ARCH_REF}
    )
    assert passed is True


# ---------------------------------------------------------------------------
# custom_initiative_equivalent


def test_custom_initiative_equivalent_when_defids_match(monkeypatch) -> None:
    _patch_baseline(
        monkeypatch,
        {
            _INIT_REF["path"]: {
                "properties": {
                    "policyDefinitions": [
                        {"policyDefinitionId": "/x/a"},
                        {"policyDefinitionId": "/x/b"},
                    ]
                }
            },
        },
    )
    observed = [
        {
            "id": "/mg/corp/initiatives/custom",
            "policyDefinitions": [
                {"policyDefinitionId": "/x/a", "policyDefinitionReferenceId": "ref1"},
                {"policyDefinitionId": "/x/b", "policyDefinitionReferenceId": "ref2"},
            ],
        }
    ]
    passed, snapshot, status = matchers.custom_initiative_equivalent(
        observed, None, {"initiative_ref": _INIT_REF}
    )
    assert passed is True
    assert status is None
    assert snapshot == {"drifted_initiatives": []}


def test_custom_initiative_drift_when_defids_differ(monkeypatch) -> None:
    _patch_baseline(
        monkeypatch,
        {
            _INIT_REF["path"]: {
                "properties": {
                    "policyDefinitions": [
                        {"policyDefinitionId": "/x/a"},
                        {"policyDefinitionId": "/x/b"},
                    ]
                }
            },
        },
    )
    observed = [
        {
            "id": "/mg/corp/initiatives/custom",
            "policyDefinitions": [
                {"policyDefinitionId": "/x/a"},
                {"policyDefinitionId": "/x/z"},  # extra
            ],
        }
    ]
    passed, snapshot, status = matchers.custom_initiative_equivalent(
        observed, None, {"initiative_ref": _INIT_REF}
    )
    assert passed is False
    assert status == "custom_initiative_drift"
    assert len(snapshot["drifted_initiatives"]) == 1
    drift = snapshot["drifted_initiatives"][0]
    assert drift["missing_defs"] == ["/x/b"]
    assert drift["extra_defs"] == ["/x/z"]


def test_custom_initiative_equivalent_target_filter(monkeypatch) -> None:
    _patch_baseline(
        monkeypatch,
        {
            _INIT_REF["path"]: {"properties": {"policyDefinitions": [{"policyDefinitionId": "/x/a"}]}},
        },
    )
    observed = [
        {"id": "/mg/corp/initiatives/unrelated", "policyDefinitions": [{"policyDefinitionId": "/y/q"}]},
        {"id": "/mg/corp/initiatives/target", "policyDefinitions": [{"policyDefinitionId": "/x/a"}]},
    ]
    passed, snapshot, status = matchers.custom_initiative_equivalent(
        observed,
        None,
        {
            "initiative_ref": _INIT_REF,
            "target_definition_id": "/mg/corp/initiatives/target",
        },
    )
    assert passed is True
    assert status is None


# ---------------------------------------------------------------------------
# _unpack_matcher_result contract


def test_unpack_two_tuple_defaults_status_override_none() -> None:
    passed, snap, override = matchers._unpack_matcher_result((True, {"a": 1}))
    assert passed is True
    assert snap == {"a": 1}
    assert override is None


def test_unpack_three_tuple_threads_status_override() -> None:
    passed, snap, override = matchers._unpack_matcher_result(
        (False, {"x": 2}, "parameter_drift")
    )
    assert passed is False
    assert snap == {"x": 2}
    assert override == "parameter_drift"
