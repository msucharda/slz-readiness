"""Deterministic rule engine: findings.json + rules/ → gaps.json.

NO LLM calls. NO network calls. Two runs over the same findings produce
byte-identical gaps.json. The ordering is stable: rules are sorted by
`rule_id`, gaps within a rule are sorted by `resource_id`.

Brownfield note (v0.6.0): if ``artifacts/<run>/mg_alias.json`` exists and
maps any canonical SLZ role to a non-null customer MG name, the engine
rewrites ``matcher.selector.scope: mg/<role>`` to use the aliased name
AND substitutes aliased names into ``expected`` lists. The contract
extends to: ``(findings.json, mg_alias.json) → gaps.json`` is
byte-identical across runs. See :mod:`slz_readiness.reconcile`.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .. import _summary, _trace
from .loaders import Rule, load_all_rules
from .matchers import _unpack_matcher_result, get_matcher
from .models import Gap

_ALIAS_FILE = "mg_alias.json"


def _load_alias_map(run_dir: Path) -> dict[str, str]:
    """Return ``{role: customer_mg}`` for every non-null alias.

    Thin wrapper around :func:`slz_readiness._alias_io.load_alias_map`
    that pins the trace label to ``evaluate``. Kept as a private name so
    existing imports inside engine.py keep working.
    """
    from .._alias_io import load_alias_map
    return load_alias_map(run_dir, trace_label="evaluate")


def _apply_alias_to_selector(
    selector: dict[str, Any], alias_map: dict[str, str]
) -> dict[str, Any]:
    """Rewrite ``scope: mg/<role>`` to ``scope: mg/<customer_mg>`` when aliased."""
    if not alias_map:
        return selector
    scope = selector.get("scope")
    if not isinstance(scope, str) or not scope.startswith("mg/"):
        return selector
    role = scope[len("mg/"):]
    aliased = alias_map.get(role)
    if not aliased:
        return selector
    return {**selector, "scope": f"mg/{aliased}"}


def _apply_alias_to_expected(expected: Any, alias_map: dict[str, str]) -> Any:
    """Substitute aliased role names inside an ``expected`` list (e.g. the
    ``mg.slz.hierarchy_shape`` required-MG list). Items not aliased pass
    through unchanged so the canonical name is still acceptable."""
    if not alias_map or not isinstance(expected, list):
        return expected
    return [alias_map.get(item, item) if isinstance(item, str) else item for item in expected]


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


def _tally_bump(tally_out: dict[str, Any] | None, *, passed: bool, status: str) -> None:
    """Bump counters in ``tally_out`` without allocating when ``None``.

    The dict is shaped for direct embedding in ``evaluate.summary.json``:
    ``{rules_evaluated, rules_passed, rules_failed, rules_unknown}``.
    """
    if tally_out is None:
        return
    tally_out["rules_evaluated"] = tally_out.get("rules_evaluated", 0) + 1
    if status == "unknown":
        tally_out["rules_unknown"] = tally_out.get("rules_unknown", 0) + 1
    elif passed:
        tally_out["rules_passed"] = tally_out.get("rules_passed", 0) + 1
    else:
        tally_out["rules_failed"] = tally_out.get("rules_failed", 0) + 1


def evaluate(
    findings: list[dict[str, Any]],
    rules: list[Rule] | None = None,
    *,
    tally_out: dict[str, Any] | None = None,
    alias_map: dict[str, str] | None = None,
) -> list[Gap]:
    """Run every rule over ``findings`` and return gaps.

    Optional ``tally_out`` is populated in-place with ``rules_evaluated`` /
    ``rules_passed`` / ``rules_failed`` / ``rules_unknown`` counters so the
    caller can summarise the run without re-iterating. Pure function; no
    side effects other than ``_trace.log`` (which is itself a no-op outside a
    tracer context).

    ``alias_map`` (role → customer-MG) enables brownfield re-targeting. An
    empty or ``None`` map means canonical SLZ scopes are used unchanged —
    byte-identical output to pre-v0.6.0 Evaluate.
    """
    rules = rules if rules is not None else load_all_rules()
    alias_map = alias_map or {}
    gaps: list[Gap] = []

    for rule in sorted(rules, key=lambda r: r.rule_id):
        selector = _apply_alias_to_selector(rule.matcher.get("selector", {}), alias_map)
        expected = _apply_alias_to_expected(rule.expected, alias_map)
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
            _tally_bump(tally_out, passed=False, status="unknown")
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
            passed, snapshot, status_override = _unpack_matcher_result(
                matcher_fn(observed, expected, rule.matcher)
            )
            status = ("compliant" if passed else (status_override or "missing"))
            _trace.log(
                "rule.fire",
                rule_id=rule.rule_id,
                resource_id="tenant",
                passed=passed,
                status=status,
            )
            _tally_bump(tally_out, passed=passed, status=status)
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
                        status=status,
                    )
                )
            continue

        # Per-resource rules produce one gap per non-compliant resource.
        for f in sorted(target_findings, key=lambda f: f.get("resource_id", "")):
            matcher_fn = get_matcher(rule.matcher["type"])
            passed, snapshot, status_override = _unpack_matcher_result(
                matcher_fn(f.get("observed_state"), expected, rule.matcher)
            )
            status = ("compliant" if passed else (status_override or "missing"))
            _trace.log(
                "rule.fire",
                rule_id=rule.rule_id,
                resource_id=f.get("resource_id", ""),
                passed=passed,
                status=status,
            )
            _tally_bump(tally_out, passed=passed, status=status)
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
                        status=status,
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
    run_scope: dict[str, Any] = {}
    if isinstance(findings, dict):
        run_scope = findings.get("run_scope") or {}
        if "findings" in findings:
            findings = findings["findings"]
    tally: dict[str, Any] = {
        "rules_evaluated": 0,
        "rules_passed": 0,
        "rules_failed": 0,
        "rules_unknown": 0,
    }
    with _trace.tracer(gaps_path.parent, phase="evaluate"):
        _trace.log("evaluate.begin", findings=len(findings))
        alias_map = _load_alias_map(gaps_path.parent)
        gaps = evaluate(findings, tally_out=tally, alias_map=alias_map)
        _trace.log("evaluate.end", gap_count=len(gaps))
        gaps_path.parent.mkdir(parents=True, exist_ok=True)
        gap_dicts = [gap_to_dict(g) for g in gaps]
        gaps_path.write_text(
            json.dumps(
                {"gaps": gap_dicts},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        _write_evaluate_summary(
            run_dir=gaps_path.parent,
            run_scope=run_scope,
            gaps=gap_dicts,
            tally=tally,
            findings_count=len(findings),
        )
    return 0 if not gaps else 1  # non-zero on gaps so CI + pipelines notice


def _top_largest_gaps(gaps: list[dict[str, Any]], n: int = 5) -> list[dict[str, Any]]:
    """Rank gaps by ``len(observed.missing)`` descending; ties by rule_id."""
    def _missing_count(g: dict[str, Any]) -> int:
        obs = g.get("observed")
        if isinstance(obs, dict):
            missing = obs.get("missing")
            if isinstance(missing, list):
                return len(missing)
        return 0
    ranked = sorted(
        gaps,
        key=lambda g: (-_missing_count(g), g.get("rule_id", ""), g.get("resource_id", "")),
    )
    return [g for g in ranked if _missing_count(g) > 0][:n]


def _brownfield_hint(gaps: list[dict[str, Any]]) -> str | None:
    """Return a single-line warning if `mg.slz.hierarchy_shape` reports
    many MGs missing — signals the tenant likely already runs a non-SLZ
    landing zone. Threshold: 10 of 14 canonical SLZ MGs missing.

    Returned string (or None if threshold not met) is appended to
    `evaluate.summary.md` so the operator sees it before gating to Plan.
    Threshold chosen so fresh-tenant fixtures (all 14 missing) trigger,
    but a legitimate CAF-aligned tenant with 3-4 MGs missing does not.
    """
    for g in gaps:
        if g.get("rule_id") != "mg.slz.hierarchy_shape":
            continue
        observed = g.get("observed")
        if not isinstance(observed, dict):
            continue
        missing = observed.get("missing")
        if isinstance(missing, list) and len(missing) >= 10:
            return (
                "> WARNING: Brownfield hint — `mg.slz.hierarchy_shape` reports "
                f"{len(missing)} of 14 SLZ MGs missing. If this tenant already "
                "operates a landing zone under different MG names, the gap list "
                "overstates the remediation cost. Run `/slz-reconcile` to map "
                "canonical SLZ roles to your tenant's actual MGs, then re-run "
                "Discover and Evaluate — they will consume `mg_alias.json` to "
                "retarget probes and selectors. See `docs/brownfield.md` for "
                "the full retargeting workflow."
            )
    return None


def _write_evaluate_summary(
    *,
    run_dir: Path,
    run_scope: dict[str, Any],
    gaps: list[dict[str, Any]],
    tally: dict[str, Any],
    findings_count: int,
) -> None:
    sev = _summary.severity_tally(gaps)
    areas = _summary.design_area_tally(gaps)
    statuses = _summary.status_tally(gaps)
    unknowns = _summary.unknown_gaps(gaps)
    largest = _top_largest_gaps(gaps)

    payload = {
        "phase": "evaluate",
        "tenant_id": run_scope.get("tenant_id"),
        "findings_count": findings_count,
        "gap_count": len(gaps),
        "by_severity": sev,
        "by_design_area": areas,
        "by_status": statuses,
        "compliance": tally,
        "top_largest_gaps": [
            {
                "rule_id": g.get("rule_id"),
                "resource_id": g.get("resource_id"),
                "missing_count": len((g.get("observed") or {}).get("missing") or []),
            }
            for g in largest
        ],
        "unknown_gaps": [
            {
                "rule_id": g.get("rule_id"),
                "resource_id": g.get("resource_id"),
                "error": (g.get("observed") or {}).get("error"),
            }
            for g in unknowns
        ],
    }
    _summary.write_json(run_dir / "evaluate.summary.json", payload)

    parts: list[str] = []
    parts.append(
        _summary.header_block(
            "SLZ Evaluate summary",
            tenant=run_scope.get("tenant_id"),
            run_id=_summary.run_id_from_path(run_dir),
        )
    )
    parts.append(
        f"**Gaps:** {len(gaps)} across {findings_count} findings. "
        f"Rules: {tally['rules_passed']} passed / {tally['rules_failed']} failed / "
        f"{tally['rules_unknown']} unknown of {tally['rules_evaluated']} evaluated."
    )
    parts.append("")
    parts.append("## By severity")
    parts.append("")
    parts.append(
        _summary.render_table(
            ["Severity", "Count"],
            [[k, sev[k]] for k in sev if sev[k] > 0] or [["(none)", 0]],
        )
    )
    parts.append("")
    parts.append("## By design area")
    parts.append("")
    parts.append(
        _summary.render_table(
            ["Design area", "Count"],
            [[k, areas[k]] for k in areas] or [["(none)", 0]],
        )
    )
    parts.append("")
    parts.append("## By status")
    parts.append("")
    parts.append(
        _summary.render_table(
            ["Status", "Count"],
            [[k, statuses[k]] for k in statuses if statuses[k] > 0] or [["(none)", 0]],
        )
    )
    parts.append("")
    if largest:
        parts.append("## Top gaps by missing count")
        parts.append("")
        parts.append(
            _summary.render_table(
                ["rule_id", "resource_id", "Missing items"],
                [
                    [
                        g.get("rule_id", ""),
                        g.get("resource_id", ""),
                        len((g.get("observed") or {}).get("missing") or []),
                    ]
                    for g in largest
                ],
            )
        )
        parts.append("")
    if unknowns:
        parts.append("## Unknown (discovery blocked)")
        parts.append("")
        parts.append(
            "These rules could not be evaluated because discovery failed; "
            "re-run with elevated access to resolve."
        )
        parts.append("")
        parts.append(
            _summary.render_table(
                ["rule_id", "resource_id", "Error"],
                [
                    [
                        g.get("rule_id", ""),
                        g.get("resource_id", ""),
                        (g.get("observed") or {}).get("error", ""),
                    ]
                    for g in unknowns
                ],
            )
        )
        parts.append("")
    parts.append("## See also")
    parts.append("")
    parts.append("- `gaps.json` — full gap list with baseline citations")
    parts.append("- `trace.jsonl` — `rule.fire` events for every rule evaluated")
    hint = _brownfield_hint(gaps)
    if hint is not None:
        parts.append("")
        parts.append(hint)
    _summary.write_md(run_dir / "evaluate.summary.md", "\n".join(parts))
    _trace.log("evaluate.summary", gap_count=len(gaps), **tally)
