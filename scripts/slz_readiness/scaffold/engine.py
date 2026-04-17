"""Scaffold engine.

Consumes gaps.json and emits Bicep + params for each gap by copying a pinned
template from scripts/scaffold/avm_templates/ and validating the caller's
parameters against the matching JSON schema. Never produces free-form Bicep.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .template_registry import ALLOWED_TEMPLATES, RULE_TO_TEMPLATE

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_DIR = REPO_ROOT / "scripts" / "scaffold" / "avm_templates"
SCHEMAS_DIR = REPO_ROOT / "scripts" / "scaffold" / "param_schemas"


class ScaffoldError(RuntimeError):
    pass


def _load_schema(template_stem: str) -> dict[str, Any]:
    schema_path = SCHEMAS_DIR / f"{template_stem}.schema.json"
    if not schema_path.exists():
        raise ScaffoldError(f"Missing param schema for template '{template_stem}'")
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _validate_params(template_stem: str, params: dict[str, Any]) -> None:
    schema = _load_schema(template_stem)
    errors = sorted(Draft202012Validator(schema).iter_errors(params), key=lambda e: e.path)
    if errors:
        msgs = "; ".join(f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors)
        raise ScaffoldError(f"Invalid params for {template_stem}: {msgs}")


def scaffold_for_gaps(gaps: list[dict[str, Any]], params_by_template: dict[str, dict[str, Any]], out_dir: Path) -> list[dict[str, Any]]:
    """Write bicep + params files for the distinct set of templates required by `gaps`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bicep").mkdir(exist_ok=True)
    (out_dir / "params").mkdir(exist_ok=True)

    needed: dict[str, list[str]] = {}
    for gap in gaps:
        rule_id = gap.get("rule_id")
        tmpl = RULE_TO_TEMPLATE.get(rule_id or "")
        if tmpl is None:
            continue
        needed.setdefault(tmpl, []).append(rule_id)

    emitted: list[dict[str, Any]] = []
    for tmpl, rule_ids in sorted(needed.items()):
        if tmpl not in ALLOWED_TEMPLATES:
            raise ScaffoldError(f"Template '{tmpl}' not in ALLOWED_TEMPLATES")
        src = TEMPLATES_DIR / f"{tmpl}.bicep"
        if not src.exists():
            raise ScaffoldError(f"Template file missing: {src}")
        params = params_by_template.get(tmpl, {})
        _validate_params(tmpl, params)

        dst_bicep = out_dir / "bicep" / f"{tmpl}.bicep"
        dst_params = out_dir / "params" / f"{tmpl}.parameters.json"
        shutil.copy2(src, dst_bicep)
        dst_params.write_text(
            json.dumps(
                {
                    "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
                    "contentVersion": "1.0.0.0",
                    "parameters": {k: {"value": v} for k, v in params.items()},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        emitted.append(
            {"template": tmpl, "bicep": str(dst_bicep.relative_to(out_dir)), "params": str(dst_params.relative_to(out_dir)), "rule_ids": sorted(set(rule_ids))}
        )
    return emitted
