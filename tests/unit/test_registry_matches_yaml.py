"""Guards that rule YAMLs agree with the scaffold template registry.

The registry (``RULE_TO_TEMPLATE``) is the source of truth for which Bicep
template a gap emits. The ``remediation_template`` field on each rule is
advisory — this test asserts the two never silently drift apart.
"""
from __future__ import annotations

from slz_readiness.evaluate.loaders import load_all_rules
from slz_readiness.scaffold.template_registry import ALLOWED_TEMPLATES, RULE_TO_TEMPLATE


def test_every_rule_has_registry_entry() -> None:
    rules = load_all_rules()
    missing = sorted(r.rule_id for r in rules if r.rule_id not in RULE_TO_TEMPLATE)
    assert not missing, (
        f"Rules without a registry entry: {missing}. "
        "Add them to scripts/slz_readiness/scaffold/template_registry.py"
    )


def test_registry_matches_rule_remediation_template() -> None:
    rules = {r.rule_id: r for r in load_all_rules()}
    disagreements: list[str] = []
    for rule_id, template in RULE_TO_TEMPLATE.items():
        rule = rules.get(rule_id)
        if rule is None:
            disagreements.append(f"{rule_id}: in registry but no YAML")
            continue
        if rule.remediation_template is None:
            continue  # rule opts out; registry still wins at scaffold time
        if rule.remediation_template != template:
            disagreements.append(
                f"{rule_id}: yaml says '{rule.remediation_template}' but registry says '{template}'"
            )
    assert not disagreements, "\n".join(disagreements)


def test_registry_templates_are_allowed() -> None:
    unknown = sorted(set(RULE_TO_TEMPLATE.values()) - ALLOWED_TEMPLATES)
    assert not unknown, f"Registry maps to non-allowed templates: {unknown}"


def test_allowed_templates_have_files() -> None:
    from pathlib import Path

    from slz_readiness.scaffold.engine import SCHEMAS_DIR, TEMPLATES_DIR

    for stem in sorted(ALLOWED_TEMPLATES):
        assert (TEMPLATES_DIR / f"{stem}.bicep").exists(), f"Missing bicep: {stem}"
        assert (SCHEMAS_DIR / f"{stem}.schema.json").exists(), f"Missing schema: {stem}"
