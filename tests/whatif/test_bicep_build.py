"""Smoke test — every Bicep template under scripts/scaffold/avm_templates/
compiles with `bicep build`. Skipped if the `bicep` CLI is not on PATH.

This enforces anti-hallucination rule #5: templates must be syntactically
valid Bicep, not free-form strings.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = sorted((REPO_ROOT / "scripts" / "scaffold" / "avm_templates").glob("*.bicep"))


@pytest.mark.skipif(shutil.which("bicep") is None, reason="bicep CLI not installed")
@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_bicep_build(template: Path, tmp_path: Path) -> None:
    out = tmp_path / f"{template.stem}.json"
    res = subprocess.run(  # noqa: S603
        ["bicep", "build", str(template), "--outfile", str(out)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"bicep build failed:\n{res.stderr}"
    assert out.exists()
