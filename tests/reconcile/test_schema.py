"""Schema validator tests for mg_alias.json."""
from __future__ import annotations

import pytest
from slz_readiness.reconcile import CANONICAL_ROLES
from slz_readiness.reconcile.schema import AliasSchemaError, empty_alias, validate


def _good_alias() -> dict[str, str | None]:
    return {role: None for role in CANONICAL_ROLES}


def test_empty_alias_is_all_null() -> None:
    alias = empty_alias()
    assert set(alias.keys()) == set(CANONICAL_ROLES)
    assert all(v is None for v in alias.values())


def test_validate_passes_all_null() -> None:
    alias = _good_alias()
    assert validate(alias) == alias


def test_rejects_top_level_non_dict() -> None:
    with pytest.raises(AliasSchemaError, match="top level"):
        validate(["corp", "platform"])


def test_missing_roles_default_to_null() -> None:
    """v0.7.1 relax: partial proposals are accepted; absent roles → null."""
    alias = _good_alias()
    del alias["corp"]
    del alias["platform"]
    out = validate(alias)
    assert out["corp"] is None
    assert out["platform"] is None
    # Other roles preserved as-is.
    assert out["management"] == alias["management"]


def test_rejects_unknown_roles() -> None:
    alias = _good_alias()
    alias["atlantis"] = "somewhere"
    with pytest.raises(AliasSchemaError, match="unknown roles"):
        validate(alias)


def test_rejects_non_string_value() -> None:
    alias = _good_alias()
    alias["corp"] = 42  # type: ignore[assignment]
    with pytest.raises(AliasSchemaError, match="must be str or null"):
        validate(alias)


def test_rejects_empty_string_value() -> None:
    alias = _good_alias()
    alias["corp"] = ""
    with pytest.raises(AliasSchemaError, match="empty-string"):
        validate(alias)


def test_strips_whitespace() -> None:
    alias = _good_alias()
    alias["corp"] = "  prod-int  "
    normalised = validate(alias)
    assert normalised["corp"] == "prod-int"


def test_rejects_duplicate_customer_mg() -> None:
    alias = _good_alias()
    alias["corp"] = "prod-int"
    alias["online"] = "prod-int"
    with pytest.raises(AliasSchemaError, match="unique"):
        validate(alias)


def test_cross_check_against_findings() -> None:
    alias = _good_alias()
    alias["corp"] = "prod-int"
    findings = {
        "findings": [
            {
                "resource_type": "microsoft.management/managementgroups.summary",
                "observed_state": {"present_ids": ["tenant-root", "prod-int", "dev"]},
            }
        ]
    }
    # passes
    validate(alias, findings=findings)

    alias["online"] = "nowhere"
    with pytest.raises(AliasSchemaError, match="not present"):
        validate(alias, findings=findings)


def test_cross_check_skipped_when_no_mg_summary() -> None:
    alias = _good_alias()
    alias["corp"] = "anything"
    # findings with no MG-summary record → membership check is skipped
    findings = {"findings": [{"resource_type": "microsoft.authorization/policyassignments"}]}
    validate(alias, findings=findings)  # does not raise
