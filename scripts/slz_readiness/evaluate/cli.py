"""`slz-evaluate` — runs the deterministic rule engine."""
from __future__ import annotations

import sys
from pathlib import Path

import click

from .engine import run


@click.command()
@click.option("--findings", "findings_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--gaps", "gaps_path", required=True, type=click.Path(path_type=Path))
def main(findings_path: Path, gaps_path: Path) -> None:
    """Evaluate a findings.json against the vendored baseline. Writes gaps.json."""
    code = run(findings_path, gaps_path)
    sys.exit(code)


if __name__ == "__main__":
    main()
