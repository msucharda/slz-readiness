"""`slz-scaffold` — consumes gaps.json + params.json and emits Bicep/params files."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .engine import ScaffoldError, scaffold_for_gaps


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
    try:
        emitted = scaffold_for_gaps(gaps, params_by_template, out_dir)
    except ScaffoldError as exc:
        click.echo(f"SCAFFOLD ERROR: {exc}", err=True)
        sys.exit(2)
    (out_dir / "scaffold.manifest.json").write_text(
        json.dumps({"emitted": emitted}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    click.echo(f"Emitted {len(emitted)} templates -> {out_dir}")


if __name__ == "__main__":
    main()
