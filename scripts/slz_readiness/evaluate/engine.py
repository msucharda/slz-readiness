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

from .loaders import Rule, load_all_rules
from .matchers import get_matcher
from .models import Gap


def _finding_selector(finding: dict[str, Any], selector: dict[str, Any]) -> bool:
    """A selector filters which findings a rule applies to."""
    for key, expected in selector.items():
        if finding.get(key) != expected:
            return False
    return True


def evaluate(findings: list[dict[str, Any]], rules: list[Rule] | None = None) -> list[Gap]:
    rules = rules if rules is not None else load_all_rules()
    gaps: list[Gap] = []

    for rule in sorted(rules, key=lambda r: r.rule_id):
        selector = rule.matcher.get("selector", {})
        target_findings = [f for f in findings if _finding_selector(f, selector)]

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
                    )
                )
            continue

        # Per-resource rules produce one gap per non-compliant resource.
        for f in sorted(target_findings, key=lambda f: f.get("resource_id", "")):
            matcher_fn = get_matcher(rule.matcher["type"])
            passed, snapshot = matcher_fn(f.get("observed_state"), rule.expected, rule.matcher)
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
    gaps = evaluate(findings)
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
