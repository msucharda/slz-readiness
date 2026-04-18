"""Tests for v0.8.0 engine drift-status threading — the engine must
propagate a matcher's ``status_override`` (e.g. ``parameter_drift``,
``custom_initiative_drift``) into the emitted gap's ``status`` field and
the tally's per-status counter."""
from __future__ import annotations

from typing import Any

from slz_readiness.evaluate import engine
from slz_readiness.evaluate.loaders import Rule
from slz_readiness.evaluate.models import BaselineRef


def _make_rule(
    rule_id: str = "test.drift_rule",
    matcher: dict[str, Any] | None = None,
) -> Rule:
    return Rule(
        rule_id=rule_id,
        design_area="policy",
        severity="medium",
        description="test",
        baseline=BaselineRef(source="test", path="p", sha="s"),
        matcher=matcher or {"type": "fake", "selector": {"resource_type": "x"}, "aggregate": "tenant"},
        expected=None,
        message="drift detected",
    )


def test_engine_threads_parameter_drift_status(monkeypatch) -> None:
    """When a matcher returns a 3-tuple with ``status_override='parameter_drift'``,
    the Gap must carry ``status='parameter_drift'`` (NOT the default
    ``'missing'``)."""

    def fake_matcher(observed: Any, expected: Any, spec: dict[str, Any]) -> tuple[bool, Any, str | None]:
        return False, {"drifted": ["k1"]}, "parameter_drift"

    from slz_readiness.evaluate import matchers
    monkeypatch.setitem(matchers.MATCHERS, "fake", fake_matcher)

    findings = [{"resource_type": "x", "resource_id": "r1", "observed_state": {}}]
    tally: dict[str, Any] = {}
    gaps = engine.evaluate(findings, rules=[_make_rule()], tally_out=tally)
    assert len(gaps) == 1
    assert gaps[0].status == "parameter_drift"
    assert tally.get("rules_failed") == 1


def test_engine_threads_custom_initiative_drift_status(monkeypatch) -> None:
    def fake_matcher(observed: Any, expected: Any, spec: dict[str, Any]) -> tuple[bool, Any, str | None]:
        return False, {"missing_defs": ["/x/b"]}, "custom_initiative_drift"

    from slz_readiness.evaluate import matchers
    monkeypatch.setitem(matchers.MATCHERS, "fake", fake_matcher)

    findings = [{"resource_type": "x", "resource_id": "r1", "observed_state": {}}]
    gaps = engine.evaluate(findings, rules=[_make_rule()])
    assert len(gaps) == 1
    assert gaps[0].status == "custom_initiative_drift"


def test_engine_preserves_missing_status_for_two_tuple_matcher(monkeypatch) -> None:
    """v0.7.x contract: a matcher returning ``(False, snap)`` still
    produces ``status='missing'``. Backwards-compatible."""

    def fake_matcher(observed: Any, expected: Any, spec: dict[str, Any]) -> tuple[bool, Any]:
        return False, {"missing": ["x"]}

    from slz_readiness.evaluate import matchers
    monkeypatch.setitem(matchers.MATCHERS, "fake", fake_matcher)

    findings = [{"resource_type": "x", "resource_id": "r1", "observed_state": {}}]
    gaps = engine.evaluate(findings, rules=[_make_rule()])
    assert len(gaps) == 1
    assert gaps[0].status == "missing"
