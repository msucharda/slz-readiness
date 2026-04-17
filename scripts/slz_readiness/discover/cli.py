"""`slz-discover` — runs every discover script and writes findings.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from . import (
    identity_rbac,
    logging_monitoring,
    mg_hierarchy,
    policy_assignments,
    sovereignty_controls,
)

DISCOVERERS = [
    mg_hierarchy,
    policy_assignments,
    identity_rbac,
    logging_monitoring,
    sovereignty_controls,
]


@click.command()
@click.option("--out", "out_path", required=True, type=click.Path(path_type=Path))
def main(out_path: Path) -> None:
    """Collect read-only findings from the tenant into findings.json."""
    findings: list = []
    for mod in DISCOVERERS:
        findings.extend(mod.discover())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"findings": findings}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    click.echo(f"Wrote {len(findings)} findings -> {out_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
