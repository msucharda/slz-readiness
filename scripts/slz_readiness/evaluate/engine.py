"""Deterministic rule engine: findings.json + rules/ → gaps.json.

NO LLM calls. NO network calls. Two runs over the same findings produce
byte-identical gaps.json. The ordering is stable: rules are sorted by
`rule_id`, gaps within a rule are sorted by `resource_id`.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .. import _trace
from .loaders import Rule, load_all_rules
from .matchers import get_matcher
from .models import Gap


def _finding_selector(finding: dict[str, Any], selector: dict[str, Any]) -> bool:
    """A selector filters which findings a rule applies to."""
    for key, expected in selector.items():
        if finding.get(key) != expected:
            return False
    return True


def _is_error_finding(finding: dict[str, Any]) -> bool:
    """Error findings are emitted by discover when a query failed (e.g.
    permission_denied). Evaluate turns these into unknown-severity gaps."""
    obs = finding.get("observed_state")
    return isinstance(obs, dict) and "error" in obs


def _unknown_gap_from_finding(rule: Rule, finding: dict[str, Any]) -> Gap:
    obs = finding.get("observed_state") or {}
    return Gap(
        rule_id=rule.rule_id,
        severity="unknown",
        design_area=rule.design_area,
        observed=obs,
        expected=rule.expected,
        baseline_ref=rule.baseline,
        resource_id=finding.get("resource_id", ""),
        message=f"Discovery blocked ({obs.get('error', 'unknown')}): rule could not be evaluated. Re-run with elevated access.",
        remediation_template=None,  # can't scaffold a fix we can't verify
        status="unknown",
    )


def evaluate(findings: list[dict[str, Any]], rules: list[Rule] | None = None) -> list[Gap]:
    rules = rules if rules is not None else load_all_rules()
    gaps: list[Gap] = []

    for rule in sorted(rules, key=lambda r: r.rule_id):
        selector = rule.matcher.get("selector", {})
        target_findings = [f for f in findings if _finding_selector(f, selector)]

        # If any finding in-scope is an error finding, emit an unknown-severity
        # gap for it — the rule's answer is "cannot determine".
        error_findings = [f for f in target_findings if _is_error_finding(f)]
        ok_findings = [f for f in target_findings if not _is_error_finding(f)]
        for f in sorted(error_findings, key=lambda f: f.get("resource_id", "")):
            gap = _unknown_gap_from_finding(rule, f)
            _trace.log(
                "rule.fire",
                rule_id=rule.rule_id,
                resource_id=gap.resource_id,
                passed=False,
                status="unknown",
            )
            gaps.append(gap)

        target_findings = ok_findings

        # "applies_to_tenant" rules collapse every matching finding into a single gap.
        if rule.matcher.get("aggregate") == "tenant":
            observed_list = [f.get("observed_state", {}) for f in target_findings]
            # Unwrap single finding: the rule expects the finding's observation shape directly.
            if len(observed_list) == 1:
                observed = observed_list[0]
            else:
                observed = observed_list
                # Flatten list-of-lists (e.g. per-scope policy assignment pages).
                if observed_list and all(isinstance(x, list) for x in observed_list):
                    observed = [x for sub in observed_list for x in sub]
            matcher_fn = get_matcher(rule.matcher["type"])
            passed, snapshot = matcher_fn(observed, rule.expected, rule.matcher)
            _trace.log(
                "rule.fire",
                rule_id=rule.rule_id,
                resource_id="tenant",
                passed=passed,
                status=("compliant" if passed else "missing"),
            )
            if not passed:
                gaps.append(
                    Gap(
                        rule_id=rule.rule_id,
                        severity=rule.severity,
                        design_area=rule.design_area,
                        observed=snapshot,
                        expected=rule.expected,
                        baseline_ref=rule.baseline,
                        resource_id="tenant",
                        message=rule.message,
                        remediation_template=rule.remediation_template,
                        status="missing",
                    )
                )
            continue

        # Per-resource rules produce one gap per non-compliant resource.
        for f in sorted(target_findings, key=lambda f: f.get("resource_id", "")):
            matcher_fn = get_matcher(rule.matcher["type"])
            passed, snapshot = matcher_fn(f.get("observed_state"), rule.expected, rule.matcher)
            _trace.log(
                "rule.fire",
                rule_id=rule.rule_id,
                resource_id=f.get("resource_id", ""),
                passed=passed,
                status=("compliant" if passed else "missing"),
            )
            if not passed:
                gaps.append(
                    Gap(
                        rule_id=rule.rule_id,
                        severity=rule.severity,
                        design_area=rule.design_area,
                        observed=snapshot,
                        expected=rule.expected,
                        baseline_ref=rule.baseline,
                        resource_id=f.get("resource_id", ""),
                        message=rule.message,
                        remediation_template=rule.remediation_template,
                        status="missing",
                    )
                )

    return gaps


def gap_to_dict(g: Gap) -> dict[str, Any]:
    d = asdict(g)
    # Flatten BaselineRef for stable JSON.
    d["baseline_ref"] = asdict(g.baseline_ref)
    return d


def run(findings_path: Path, gaps_path: Path) -> int:
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    if isinstance(findings, dict) and "findings" in findings:
        findings = findings["findings"]
    with _trace.tracer(gaps_path.parent, phase="evaluate"):
        _trace.log("evaluate.begin", findings=len(findings))
        gaps = evaluate(findings)
        _trace.log("evaluate.end", gap_count=len(gaps))
    gaps_path.parent.mkdir(parents=True, exist_ok=True)
    gaps_path.write_text(
        json.dumps(
            {"gaps": [gap_to_dict(g) for g in gaps]},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0 if not gaps else 1  # non-zero on gaps so CI + pipelines notice
