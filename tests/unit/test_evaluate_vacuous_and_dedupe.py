"""Regression tests for slz-demo run 20260419T120215Z findings:

* N1 — vacuous passes (rule fires ``passed=True`` against an empty
  observation set) must be tracked separately from real passes so the
  headline pass count does not lie about coverage.
* L3 — ``evaluate.defid_load_skip`` events must be deduped by
  ``(ref_path, reason)`` within a single ``evaluate()`` call to keep
  trace.jsonl readable when a baseline file is referenced by multiple
  archetype rules.
"""
from __future__ import annotations

from typing import Any

from slz_readiness.evaluate import engine, matchers
from slz_readiness.evaluate.loaders import Rule
from slz_readiness.evaluate.models import BaselineRef


def _rule(rule_id: str, *, aggregate: bool) -> Rule:
    matcher: dict[str, Any] = {
        "type": "fake_pass_on_empty",
        "selector": {"resource_type": "x"},
    }
    if aggregate:
        matcher["aggregate"] = "tenant"
    return Rule(
        rule_id=rule_id,
        design_area="logging",
        severity="high",
        description="test",
        baseline=BaselineRef(source="test", path="p", sha="s"),
        matcher=matcher,
        expected=None,
        message="missing",
    )


def _install_pass_on_empty(monkeypatch) -> None:
    def fake_matcher(
        observed: Any, expected: Any, spec: dict[str, Any]
    ) -> tuple[bool, Any, str | None]:
        # Pass iff observed is empty/falsy — simulates a rule that
        # vacuously satisfies because there is nothing to check.
        return (not observed), observed, None

    monkeypatch.setitem(matchers.MATCHERS, "fake_pass_on_empty", fake_matcher)


def test_vacuous_pass_is_tracked_separately_from_real_pass(monkeypatch) -> None:
    """N1: an aggregate=tenant rule that passes with zero observations
    increments ``rules_passed_vacuous`` so the summary can footnote it."""
    _install_pass_on_empty(monkeypatch)
    tally: dict[str, Any] = {
        "rules_evaluated": 0,
        "rules_passed": 0,
        "rules_failed": 0,
        "rules_unknown": 0,
    }
    # No findings of resource_type=x → matcher receives []/empty → passes vacuously.
    engine.evaluate([], rules=[_rule("test.vacuous", aggregate=True)], tally_out=tally)
    assert tally["rules_passed"] == 1
    assert tally["rules_passed_vacuous"] == 1


def test_real_pass_is_not_marked_vacuous(monkeypatch) -> None:
    """N1: a pass against a non-empty observation set must NOT be marked
    vacuous — the rule actually saw and approved something."""
    def matcher_always_pass(
        observed: Any, expected: Any, spec: dict[str, Any]
    ) -> tuple[bool, Any, str | None]:
        return True, observed, None

    monkeypatch.setitem(matchers.MATCHERS, "fake_real_pass", matcher_always_pass)
    rule = Rule(
        rule_id="test.real",
        design_area="logging",
        severity="high",
        description="test",
        baseline=BaselineRef(source="test", path="p", sha="s"),
        matcher={
            "type": "fake_real_pass",
            "selector": {"resource_type": "x"},
            "aggregate": "tenant",
        },
        expected=None,
        message="missing",
    )
    findings = [
        {
            "resource_type": "x",
            "resource_id": "tenant",
            "observed_state": {"value": "non-empty"},
        }
    ]
    tally: dict[str, Any] = {
        "rules_evaluated": 0,
        "rules_passed": 0,
        "rules_failed": 0,
        "rules_unknown": 0,
    }
    engine.evaluate(findings, rules=[rule], tally_out=tally)
    assert tally["rules_passed"] == 1
    assert tally.get("rules_passed_vacuous", 0) == 0


def test_defid_load_skip_dedupes_within_one_evaluate_call() -> None:
    """L3: emitting the same (ref_path, reason) twice in one run logs once."""
    matchers._reset_defid_skip_dedupe()
    logged: list[dict[str, Any]] = []

    from slz_readiness import _trace as trace_mod

    original_log = trace_mod.log

    def capture(event: str, **kwargs: Any) -> None:
        if event == "evaluate.defid_load_skip":
            logged.append(kwargs)
        original_log(event, **kwargs)

    trace_mod.log = capture  # type: ignore[assignment]
    try:
        matchers._emit_defid_skip("foo", "platform/alz/policy_assignments/foo.json", "missing")
        matchers._emit_defid_skip("foo", "platform/alz/policy_assignments/foo.json", "missing")
        matchers._emit_defid_skip("bar", "platform/alz/policy_assignments/bar.json", "missing")
        matchers._emit_defid_skip("foo", "platform/alz/policy_assignments/foo.json", "missing")
    finally:
        trace_mod.log = original_log  # type: ignore[assignment]
    # foo emitted once, bar emitted once → 2 total despite 4 calls.
    assert len(logged) == 2
    assert {entry["ref"] for entry in logged} == {
        "platform/alz/policy_assignments/foo.json",
        "platform/alz/policy_assignments/bar.json",
    }


def test_defid_skip_dedupe_resets_between_evaluate_calls(monkeypatch) -> None:
    """L3: ``_reset_defid_skip_dedupe`` (called from ``evaluate()``) clears
    the cache so a follow-up run re-emits skip events."""
    matchers._emit_defid_skip("baz", "x/y/baz.json", "missing")
    assert ("x/y/baz.json", "missing") in matchers._defid_skip_seen
    matchers._reset_defid_skip_dedupe()
    assert matchers._defid_skip_seen == set()
