"""`slz-reconcile` — schema-gated writer of ``mg_alias.json``.

Usage::

    # Greenfield — writes every role → null and exits 0. No LLM involved.
    slz-reconcile --mode greenfield \\
        --findings artifacts/<run>/findings.json \\
        --out artifacts/<run>/mg_alias.json

    # Brownfield — reads a proposal JSON (produced by the /slz-reconcile prompt
    # after accepted ask_user gates), validates against findings, writes.
    slz-reconcile --mode brownfield \\
        --findings artifacts/<run>/findings.json \\
        --proposal artifacts/<run>/mg_alias.proposal.json \\
        --out artifacts/<run>/mg_alias.json

This CLI performs **zero LLM calls** and **zero Azure calls**. The LLM
inference lives in the Copilot prompt surface; this binary only
validates + writes. That separation keeps ``cli.py`` unit-testable
without mocking model calls and keeps the ``post-tool-use`` hook's
guard-rail story simple.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from .. import _trace
from . import CANONICAL_ROLES
from .schema import AliasSchemaError, empty_alias, validate
from .summary import write as write_summary


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_alias(out: Path, alias: dict[str, str | None]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(alias, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@click.command(name="slz-reconcile")
@click.option(
    "--mode",
    type=click.Choice(["greenfield", "brownfield"]),
    required=True,
    help="Scope mode. CLI refuses to guess; the Copilot surface sets this from an ask_user gate.",
)
@click.option(
    "--findings",
    "findings_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to findings.json produced by /slz-discover.",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="Where to write mg_alias.json (typically artifacts/<run>/mg_alias.json).",
)
@click.option(
    "--proposal",
    "proposal_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=False,
    help="Brownfield only: accepted-mapping proposal JSON written by the /slz-reconcile prompt.",
)
def main(
    mode: str,
    findings_path: Path,
    out_path: Path,
    proposal_path: Path | None,
) -> None:
    run_dir = out_path.parent
    with _trace.tracer(run_dir, phase="reconcile"):
        _trace.log("reconcile.begin", mode=mode)

        findings = _load_json(findings_path)

        if mode == "greenfield":
            if proposal_path is not None:
                click.echo(
                    "--proposal is only valid with --mode brownfield",
                    err=True,
                )
                sys.exit(2)
            alias = empty_alias()
            _trace.log("reconcile.greenfield.shortcircuit", roles=len(CANONICAL_ROLES))
        else:
            if proposal_path is None:
                click.echo(
                    "--mode brownfield requires --proposal "
                    "(emit it from the /slz-reconcile prompt)",
                    err=True,
                )
                sys.exit(2)
            raw = _load_json(proposal_path)
            try:
                alias = validate(raw, findings=findings)
            except AliasSchemaError as exc:
                _trace.log("reconcile.schema.reject", error=str(exc))
                click.echo(f"mg_alias.json rejected: {exc}", err=True)
                sys.exit(3)
            _trace.log(
                "reconcile.brownfield.accepted",
                mapped=sum(1 for v in alias.values() if v is not None),
            )

        _write_alias(out_path, alias)
        summary = write_summary(run_dir, mode=mode, alias=alias)
        _trace.log(
            "reconcile.end",
            **{
                k: summary[k]
                for k in (
                    "roles_mapped_count",
                    "roles_unmapped_count",
                    "unique_customer_mgs_claimed",
                )
            },
        )

    click.echo(f"wrote {out_path}")


if __name__ == "__main__":
    main()
