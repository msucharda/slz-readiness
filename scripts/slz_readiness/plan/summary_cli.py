"""`slz-plan-summary` — emit plan.summary.{json,md} from gaps.json.

Deterministic. Zero LLM. Zero network. Lives next to the LLM-narrated
``plan.md`` so the agent can reference human-readable numbers without
re-computing them (which the post-tool-use citation guard would strip).

The output filenames are chosen so the post-tool-use hook's
``endswith("plan.md")`` check does NOT match ``plan.summary.md`` — verified
by ``tests/test_hooks.py::test_plan_summary_not_filtered``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from .. import _summary, _trace

# Foundational rule ids used to derive the readiness-snapshot block. Each maps
# to a one-line human label. Kept here (not in a rule YAML) because this is
# presentation, not detection logic.
_FOUNDATION_RULES: list[tuple[str, str]] = [
    ("mg.slz.hierarchy_shape", "SLZ management-group hierarchy"),
    ("identity.platform_identity_mg_exists", "Platform identity MG"),
    ("logging.management_mg_exists", "Management MG (for central logging)"),
    ("logging.management_la_workspace_exists", "Central Log Analytics workspace"),
    ("policy.slz.sovereign_root_policies_applied", "Sovereign root policies"),
]

# Order in which design areas should be remediated. Gaps from areas not in
# this list are appended in alphabetical order.
_DESIGN_AREA_ORDER: list[str] = [
    "mg",
    "identity",
    "logging",
    "sovereignty",
    "policy",
    "archetype",
]


def _group_by_area(gaps: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for g in gaps:
        groups.setdefault(g.get("design_area") or "unknown", []).append(g)
    for area in groups:
        groups[area].sort(key=lambda g: (g.get("rule_id", ""), g.get("resource_id", "")))
    return groups


def _foundation_status(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For each foundational rule, report present/missing/unknown."""
    by_rule: dict[str, list[dict[str, Any]]] = {}
    for g in gaps:
        by_rule.setdefault(g.get("rule_id", ""), []).append(g)
    rows: list[dict[str, Any]] = []
    for rule_id, label in _FOUNDATION_RULES:
        matches = by_rule.get(rule_id, [])
        if not matches:
            state = "ok"
        elif any(g.get("status") == "unknown" for g in matches):
            state = "unknown"
        else:
            state = "missing"
        rows.append({"rule_id": rule_id, "label": label, "state": state, "gap_count": len(matches)})
    return rows


def _ordered_areas(groups: dict[str, list[dict[str, Any]]]) -> list[str]:
    present = list(groups.keys())
    ranked = [a for a in _DESIGN_AREA_ORDER if a in groups]
    extras = sorted(a for a in present if a not in ranked)
    return [*ranked, *extras]


def _render_md(
    *,
    tenant: str | None,
    run_id: str,
    gaps: list[dict[str, Any]],
    foundation: list[dict[str, Any]],
    evaluate_summary: dict[str, Any] | None,
) -> str:
    sev = _summary.severity_tally(gaps)
    unknowns = _summary.unknown_gaps(gaps)
    groups = _group_by_area(gaps)

    parts: list[str] = []
    parts.append(
        _summary.header_block(
            "SLZ Plan summary",
            tenant=tenant,
            run_id=run_id,
        )
    )

    parts.append("## Readiness snapshot")
    parts.append("")
    glyph = {
        "ok": _summary.GLYPH_OK,
        "missing": _summary.GLYPH_FAIL,
        "unknown": _summary.GLYPH_WARN,
    }
    parts.append(
        _summary.render_table(
            ["Foundation", "State", "rule_id"],
            [
                [
                    row["label"],
                    f"{glyph.get(row['state'], row['state'])} {row['state']}",
                    row["rule_id"],
                ]
                for row in foundation
            ],
        )
    )
    parts.append("")
    parts.append(
        _summary.render_table(
            ["Severity", "Count"],
            [[k, sev[k]] for k in sev if sev[k] > 0] or [["(none)", 0]],
        )
    )
    parts.append("")

    ordered = _ordered_areas(groups)
    parts.append("## Order of operations")
    parts.append("")
    if not groups:
        parts.append("No gaps to remediate against the current baseline.")
        parts.append("")
    else:
        parts.append(
            "Address design areas in the order below. Within each area, gaps are "
            "sorted by rule_id then resource_id for deterministic execution."
        )
        parts.append("")
        for i, area in enumerate(ordered, start=1):
            area_gaps = groups[area]
            parts.append(f"{i}. **{area}** -- {len(area_gaps)} gap(s)")
            for g in area_gaps:
                rid = g.get("rule_id", "")
                res = g.get("resource_id", "")
                status = g.get("status", "missing")
                parts.append(f"   - `{rid}` at `{res}` (status: {status})")
            parts.append("")

    if unknowns:
        parts.append("## Discovery blind spots")
        parts.append("")
        parts.append(
            "The following rules could not be evaluated; re-run discovery with "
            "elevated access before trusting the rest of this plan."
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

    if evaluate_summary is not None:
        compliance = evaluate_summary.get("compliance") or {}
        if compliance:
            parts.append("## Rule compliance")
            parts.append("")
            parts.append(
                f"Evaluated {compliance.get('rules_evaluated', 0)} rules: "
                f"{compliance.get('rules_passed', 0)} passed, "
                f"{compliance.get('rules_failed', 0)} failed, "
                f"{compliance.get('rules_unknown', 0)} unknown."
            )
            parts.append("")

    parts.append("## See also")
    parts.append("")
    parts.append("- `plan.md` -- LLM-narrated plan (citation-guarded)")
    parts.append("- `gaps.json` -- full gap list")
    parts.append("- `evaluate.summary.md` -- source counts for this snapshot")
    return "\n".join(parts)


@click.command()
@click.option("--gaps", "gaps_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--evaluate-summary",
    "eval_summary_path",
    required=False,
    type=click.Path(path_type=Path),
    help="Optional evaluate.summary.json for cross-referencing compliance counts.",
)
@click.option(
    "--out-dir",
    "out_dir",
    required=False,
    type=click.Path(path_type=Path),
    help="Run directory. Defaults to the parent of --gaps.",
)
def main(gaps_path: Path, eval_summary_path: Path | None, out_dir: Path | None) -> None:
    """Emit plan.summary.{json,md} deterministically from gaps.json."""
    gaps_doc = json.loads(gaps_path.read_text(encoding="utf-8"))
    gaps = gaps_doc.get("gaps", gaps_doc) if isinstance(gaps_doc, dict) else gaps_doc
    if not isinstance(gaps, list):
        raise click.ClickException(f"Unexpected gaps.json shape: {type(gaps).__name__}")

    run_dir = out_dir or gaps_path.parent
    run_dir.mkdir(parents=True, exist_ok=True)

    evaluate_summary: dict[str, Any] | None = None
    tenant: str | None = None
    if eval_summary_path is not None and Path(eval_summary_path).exists():
        evaluate_summary = json.loads(Path(eval_summary_path).read_text(encoding="utf-8"))
        tenant = (evaluate_summary or {}).get("tenant_id")
    else:
        default = run_dir / "evaluate.summary.json"
        if default.exists():
            evaluate_summary = json.loads(default.read_text(encoding="utf-8"))
            tenant = (evaluate_summary or {}).get("tenant_id")

    foundation = _foundation_status(gaps)
    payload: dict[str, Any] = {
        "phase": "plan",
        "tenant_id": tenant,
        "gap_count": len(gaps),
        "by_severity": _summary.severity_tally(gaps),
        "by_design_area": _summary.design_area_tally(gaps),
        "by_status": _summary.status_tally(gaps),
        "foundation": foundation,
        "unknown_gaps": [
            {
                "rule_id": g.get("rule_id"),
                "resource_id": g.get("resource_id"),
                "error": (g.get("observed") or {}).get("error"),
            }
            for g in _summary.unknown_gaps(gaps)
        ],
    }
    with _trace.tracer(run_dir, phase="plan"):
        _summary.write_json(run_dir / "plan.summary.json", payload)
        md = _render_md(
            tenant=tenant,
            run_id=_summary.run_id_from_path(run_dir),
            gaps=gaps,
            foundation=foundation,
            evaluate_summary=evaluate_summary,
        )
        _summary.write_md(run_dir / "plan.summary.md", md)
        _trace.log("plan.summary", gap_count=len(gaps))
    click.echo(f"Wrote plan.summary.md and plan.summary.json -> {run_dir}")


if __name__ == "__main__":
    main()
