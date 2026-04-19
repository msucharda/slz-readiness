"""Phase A dedupe tests — an error finding must not co-exist with a
missing/failing gap for the same (rule_id, resource_id).

Bug surfaced in `slz-demo` run 20260419T070007Z where
`logging.management_la_workspace_exists` appeared twice in `gaps.json`
(once `unknown`/`unknown`, once `missing`/`high` for `resource_id=tenant`)
because the underlying Log Analytics Resource Graph query both errored
(timeout) AND returned an empty row-set, causing Evaluate to fire both
the per-error `unknown` branch AND the `aggregate=tenant` `missing`
branch over the residual (empty) ok-findings universe.
"""
from __future__ import annotations

from typing import Any

from slz_readiness.evaluate import engine
from slz_readiness.evaluate.loaders import Rule
from slz_readiness.evaluate.models import BaselineRef


def _rule(rule_id: str, *, aggregate: bool) -> Rule:
    matcher: dict[str, Any] = {
        "type": "fake",
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


def _install_fake_matcher(monkeypatch, *, passes: bool = False) -> None:
    def fake_matcher(
        observed: Any, expected: Any, spec: dict[str, Any]
    ) -> tuple[bool, Any, str | None]:
        return passes, observed, None

    from slz_readiness.evaluate import matchers
    monkeypatch.setitem(matchers.MATCHERS, "fake", fake_matcher)


def test_aggregate_tenant_rule_skips_missing_when_any_finding_errored(monkeypatch) -> None:
    """Aggregate=tenant rule: one error finding + zero ok findings should
    emit exactly one `unknown` gap — not also a tenant-scoped `missing`."""
    _install_fake_matcher(monkeypatch, passes=False)
    findings = [
        {
            "resource_type": "x",
            "resource_id": "tenant",
            "observed_state": {"error": "timeout"},
        },
    ]
    gaps = engine.evaluate(findings, rules=[_rule("test.la_agg", aggregate=True)])
    assert len(gaps) == 1
    assert gaps[0].status == "unknown"
    assert gaps[0].resource_id == "tenant"


def test_aggregate_tenant_skips_missing_with_error_plus_empty_ok_finding(monkeypatch) -> None:
    """Aggregate=tenant rule: one error finding AND one empty ok finding
    (both would otherwise lead to a `missing` tenant gap) should STILL
    produce only the `unknown` gap — the error dominates."""
    _install_fake_matcher(monkeypatch, passes=False)
    findings = [
        {
            "resource_type": "x",
            "resource_id": "tenant",
            "observed_state": {"error": "timeout"},
        },
        {
            "resource_type": "x",
            "resource_id": "tenant",
            "observed_state": {},
        },
    ]
    gaps = engine.evaluate(findings, rules=[_rule("test.la_agg", aggregate=True)])
    assert len(gaps) == 1
    assert gaps[0].status == "unknown"


def test_per_resource_dedup_error_and_failing_ok_on_same_resource(monkeypatch) -> None:
    """Per-resource rule: same resource_id appears as error AND as
    failing ok finding → exactly one `unknown` gap (the ok-failure is
    suppressed)."""
    _install_fake_matcher(monkeypatch, passes=False)
    findings = [
        {
            "resource_type": "x",
            "resource_id": "res1",
            "observed_state": {"error": "permission_denied"},
        },
        {
            "resource_type": "x",
            "resource_id": "res1",
            "observed_state": {"anything": "nonmatching"},
        },
    ]
    gaps = engine.evaluate(findings, rules=[_rule("test.per_res", aggregate=False)])
    assert len(gaps) == 1
    assert gaps[0].status == "unknown"
    assert gaps[0].resource_id == "res1"


def test_per_resource_distinct_resources_still_both_emit(monkeypatch) -> None:
    """Regression: the dedupe keys on (rule_id, resource_id), so a
    separate failing resource MUST still produce its own `missing` gap."""
    _install_fake_matcher(monkeypatch, passes=False)
    findings = [
        {
            "resource_type": "x",
            "resource_id": "res_err",
            "observed_state": {"error": "timeout"},
        },
        {
            "resource_type": "x",
            "resource_id": "res_ok",
            "observed_state": {},
        },
    ]
    gaps = engine.evaluate(findings, rules=[_rule("test.per_res", aggregate=False)])
    statuses = sorted((g.resource_id, g.status) for g in gaps)
    assert statuses == [("res_err", "unknown"), ("res_ok", "missing")]


def test_aggregate_no_errors_still_emits_missing(monkeypatch) -> None:
    """Regression: with zero error findings, the aggregate branch must
    still fire normally."""
    _install_fake_matcher(monkeypatch, passes=False)
    findings = [
        {
            "resource_type": "x",
            "resource_id": "tenant",
            "observed_state": {},
        },
    ]
    gaps = engine.evaluate(findings, rules=[_rule("test.la_agg", aggregate=True)])
    assert len(gaps) == 1
    assert gaps[0].status == "missing"
    assert gaps[0].resource_id == "tenant"
