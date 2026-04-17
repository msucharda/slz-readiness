"""Rule-YAML and baseline-file loaders. Raises precisely when anything drifts."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import BaselineRef

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_DIR = REPO_ROOT / "data" / "baseline" / "alz-library"
RULES_DIR = REPO_ROOT / "scripts" / "evaluate" / "rules"
MANIFEST_FILE = BASELINE_DIR / "_manifest.json"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    design_area: str
    severity: str
    description: str
    baseline: BaselineRef
    matcher: dict[str, Any]          # matcher spec (type + params)
    expected: Any                    # expected value/shape the matcher compares against
    message: str
    remediation_template: str | None = None
    source_file: Path | None = None  # path to the rule YAML itself


class RuleLoadError(Exception):
    """Raised when a rule YAML is malformed or references a missing baseline file."""


def load_manifest() -> dict[str, dict[str, Any]]:
    if not MANIFEST_FILE.exists():
        raise RuleLoadError(
            f"Baseline manifest missing: {MANIFEST_FILE}. "
            "Run `python -m slz_readiness.evaluate.vendor_baseline` first."
        )
    return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))["files"]


def resolve_baseline_file(ref: BaselineRef, manifest: dict[str, dict[str, Any]]) -> Path:
    """Return the on-disk path for a baseline reference; raise if SHA drifts."""
    entry = manifest.get(ref.path)
    if entry is None:
        raise RuleLoadError(f"Baseline file not vendored: {ref.path}")
    if entry["git_sha"] != ref.sha:
        raise RuleLoadError(
            f"Baseline SHA mismatch for {ref.path}: rule pins {ref.sha} but vendored is {entry['git_sha']}"
        )
    return BASELINE_DIR / ref.path


def load_rule(path: Path, manifest: dict[str, dict[str, Any]]) -> Rule:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("rule_id", "design_area", "severity", "description", "baseline", "matcher", "expected", "message"):
        if key not in data:
            raise RuleLoadError(f"{path}: missing required field '{key}'")
    b = data["baseline"]
    for key in ("source", "path", "sha"):
        if key not in b:
            raise RuleLoadError(f"{path}: baseline.{key} required")
    ref = BaselineRef(source=b["source"], path=b["path"], sha=b["sha"])
    resolve_baseline_file(ref, manifest)  # raises if unresolved
    return Rule(
        rule_id=data["rule_id"],
        design_area=data["design_area"],
        severity=data["severity"],
        description=data["description"],
        baseline=ref,
        matcher=data["matcher"],
        expected=data["expected"],
        message=data["message"],
        remediation_template=data.get("remediation_template"),
        source_file=path,
    )


def load_all_rules() -> list[Rule]:
    manifest = load_manifest()
    rules: list[Rule] = []
    seen_ids: set[str] = set()
    for path in sorted(RULES_DIR.rglob("*.yml")):
        rule = load_rule(path, manifest)
        if rule.rule_id in seen_ids:
            raise RuleLoadError(f"Duplicate rule_id '{rule.rule_id}' in {path}")
        seen_ids.add(rule.rule_id)
        rules.append(rule)
    return rules


def read_baseline_json(ref: BaselineRef, manifest: dict[str, dict[str, Any]] | None = None) -> Any:
    manifest = manifest or load_manifest()
    return json.loads(resolve_baseline_file(ref, manifest).read_text(encoding="utf-8"))
