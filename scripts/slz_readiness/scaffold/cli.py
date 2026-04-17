"""`slz-scaffold` — consumes gaps.json + params.json and emits Bicep/params files."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from .. import _summary, _trace
from .engine import ScaffoldError, scaffold_for_gaps
from .template_registry import RULE_TO_TEMPLATE

# Human-readable order the deployment block recommends.
_DEPLOY_ORDER: list[str] = [
    "management-groups",
    "log-analytics",
    "sovereignty-global-policies",
    "archetype-policies",
    "sovereignty-confidential-policies",
    "policy-assignment",
    "role-assignment",
]


def _unscaffolded_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return gaps the scaffold engine refuses to emit Bicep for.

    Two buckets (preserved distinctly in the JSON summary):

    * ``status == "unknown"`` — discovery couldn't verify; cannot scaffold a
      fix we can't verify.
    * no ``RULE_TO_TEMPLATE`` entry — no template covers this rule yet.
    """
    out: list[dict[str, Any]] = []
    for g in gaps:
        rule_id = g.get("rule_id", "")
        status = g.get("status", "missing")
        if status == "unknown":
            out.append({**g, "_reason": "unknown"})
            continue
        if rule_id not in RULE_TO_TEMPLATE:
            out.append({**g, "_reason": "no_template"})
    out.sort(key=lambda g: (g.get("rule_id", ""), g.get("resource_id", "")))
    return out


def _deploy_commands(emitted: list[dict[str, Any]]) -> list[str]:
    """Build the ``what-if`` + ``create`` command block for each emitted template."""
    by_order = sorted(
        emitted,
        key=lambda e: (
            _DEPLOY_ORDER.index(e["template"]) if e["template"] in _DEPLOY_ORDER else 99,
            e.get("scope", ""),
        ),
    )
    lines: list[str] = []
    for e in by_order:
        bicep = e.get("bicep", "")
        params = e.get("params", "")
        lines.append(
            "# what-if (always run this first)\n"
            f"az deployment mg what-if --management-group-id <mg-id> \\\n"
            f"    --template-file {bicep} \\\n"
            f"    --parameters @{params}\n"
        )
        lines.append(
            "# create (only after what-if is reviewed)\n"
            f"az deployment mg create --management-group-id <mg-id> \\\n"
            f"    --template-file {bicep} \\\n"
            f"    --parameters @{params}\n"
        )
    return lines


def _write_scaffold_summary(
    *,
    out_dir: Path,
    gaps: list[dict[str, Any]],
    emitted: list[dict[str, Any]],
    warnings: list[str],
) -> None:
    unscaffolded = _unscaffolded_gaps(gaps)
    payload = {
        "phase": "scaffold",
        "gap_count": len(gaps),
        "emitted_count": len(emitted),
        "warning_count": len(warnings),
        "emitted": emitted,
        "warnings": warnings,
        "unscaffolded": [
            {
                "rule_id": g.get("rule_id"),
                "resource_id": g.get("resource_id"),
                "status": g.get("status"),
                "reason": g.get("_reason"),
            }
            for g in unscaffolded
        ],
    }
    _summary.write_json(out_dir / "scaffold.summary.json", payload)

    parts: list[str] = []
    parts.append(
        _summary.header_block(
            "SLZ Scaffold summary",
            run_id=_summary.run_id_from_path(out_dir),
        )
    )
    parts.append(
        f"**Emitted:** {len(emitted)} template(s). "
        f"**Warnings:** {len(warnings)}. "
        f"**Unscaffolded:** {len(unscaffolded)} gap(s)."
    )
    parts.append("")
    parts.append("## Emitted templates")
    parts.append("")
    parts.append(
        _summary.render_table(
            ["Template", "Scope", "Rules closed", "Bicep", "Params"],
            [
                [
                    e.get("template", ""),
                    e.get("scope", ""),
                    ", ".join(e.get("rule_ids") or []),
                    e.get("bicep", ""),
                    e.get("params", ""),
                ]
                for e in emitted
            ],
        )
        if emitted
        else "(none)"
    )
    parts.append("")
    if warnings:
        parts.append("## Warnings")
        parts.append("")
        for w in warnings:
            parts.append(f"- {w}")
        parts.append("")
    if unscaffolded:
        parts.append("## Gaps NOT scaffolded")
        parts.append("")
        parts.append(
            "These gaps did not produce Bicep output. `unknown` gaps require "
            "elevated discovery; `no_template` gaps need a new entry in "
            "`scripts/slz_readiness/scaffold/template_registry.py`."
        )
        parts.append("")
        parts.append(
            _summary.render_table(
                ["rule_id", "resource_id", "Reason", "Status"],
                [
                    [
                        g.get("rule_id", ""),
                        g.get("resource_id", ""),
                        g.get("_reason", ""),
                        g.get("status", ""),
                    ]
                    for g in unscaffolded
                ],
            )
        )
        parts.append("")
    if emitted:
        parts.append("## Deployment commands")
        parts.append("")
        parts.append(
            "Run `what-if` for every template before `create`. Replace "
            "`<mg-id>` with the target management-group id."
        )
        parts.append("")
        parts.append("```bash")
        parts.extend(line.rstrip() for line in _deploy_commands(emitted))
        parts.append("```")
        parts.append("")
    parts.append("## See also")
    parts.append("")
    parts.append("- `scaffold.manifest.json` -- machine-readable emit manifest")
    parts.append("- `bicep/` / `params/` -- generated files")
    parts.append("- `trace.jsonl` -- `template.emit` events")
    _summary.write_md(out_dir / "scaffold.summary.md", "\n".join(parts))
    _trace.log(
        "scaffold.summary",
        emitted_count=len(emitted),
        warning_count=len(warnings),
        unscaffolded_count=len(unscaffolded),
    )


def _write_run_rollup(out_dir: Path) -> None:
    """Concatenate available phase summaries into ``run.summary.md``.

    Silently skips phases whose summary file is absent (e.g. a fresh run that
    only reached Discover). Idempotent — overwrites on re-run.
    """
    sections = [
        ("discover.summary.md", "Discover"),
        ("evaluate.summary.md", "Evaluate"),
        ("plan.summary.md", "Plan"),
        ("scaffold.summary.md", "Scaffold"),
    ]
    present = [(f, label) for f, label in sections if (out_dir / f).exists()]
    if not present:
        return
    parts: list[str] = []
    parts.append(
        _summary.header_block(
            "SLZ Run summary",
            run_id=_summary.run_id_from_path(out_dir),
            extra={"phases": ",".join(lbl for _, lbl in present)},
        )
    )
    parts.append(
        "Concatenated phase summaries. Each source file remains in this "
        "directory for machine consumption."
    )
    parts.append("")
    for fname, _label in present:
        parts.append("---")
        parts.append("")
        parts.append(f"<!-- source: {fname} -->")
        body = (out_dir / fname).read_text(encoding="utf-8")
        parts.append(body.rstrip())
        parts.append("")
    _summary.write_md(out_dir / "run.summary.md", "\n".join(parts))
    _trace.log("run.summary", phases=[lbl for _, lbl in present])


@click.command()
@click.option("--gaps", "gaps_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--params",
    "params_path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="JSON file: { '<template-stem>': { param: value, ... }, ... }",
)
@click.option("--out", "out_dir", required=True, type=click.Path(path_type=Path))
def main(gaps_path: Path, params_path: Path, out_dir: Path) -> None:
    gaps_doc = json.loads(gaps_path.read_text(encoding="utf-8"))
    gaps = gaps_doc.get("gaps", gaps_doc) if isinstance(gaps_doc, dict) else gaps_doc
    params_by_template = json.loads(params_path.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    with _trace.tracer(out_dir, phase="scaffold"):
        _trace.log("scaffold.begin", gap_count=len(gaps))
        try:
            emitted, warnings = scaffold_for_gaps(gaps, params_by_template, out_dir)
        except ScaffoldError as exc:
            click.echo(f"SCAFFOLD ERROR: {exc}", err=True)
            sys.exit(2)
        _trace.log("scaffold.end", emitted_count=len(emitted), warning_count=len(warnings))
        (out_dir / "scaffold.manifest.json").write_text(
            json.dumps({"emitted": emitted, "warnings": warnings}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_scaffold_summary(out_dir=out_dir, gaps=gaps, emitted=emitted, warnings=warnings)
        _write_run_rollup(out_dir)
    click.echo(f"Emitted {len(emitted)} templates -> {out_dir}")
    if warnings:
        click.echo(f"  with {len(warnings)} warnings (see scaffold.manifest.json)")


if __name__ == "__main__":
    main()
