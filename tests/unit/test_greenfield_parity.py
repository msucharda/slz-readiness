"""Greenfield-parity test (v0.7.1, gap-report finding #5).

Asserts the v0.7.0 brownfield retargeting work has zero observable side
effect on greenfield runs:

* ``evaluate(findings)`` (no alias_map) ≡ ``evaluate(findings, alias_map={})``
* ``evaluate.run`` over a run dir with no ``mg_alias.json`` produces the
  same gaps as a run dir with an all-null ``mg_alias.json``
* ``discover._alias.load_aliased_mgs`` returns ``[]`` for both
  "no file" and "all-null file" cases.

If any of these regress, the brownfield code path has leaked into the
greenfield contract and pre-v0.7.0 users will see different output.
"""
from __future__ import annotations

import json
from pathlib import Path

from slz_readiness.discover._alias import load_aliased_mgs
from slz_readiness.evaluate.engine import evaluate, gap_to_dict
from slz_readiness.evaluate.engine import run as evaluate_run
from slz_readiness.reconcile import CANONICAL_ROLES

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load_findings(name: str) -> list:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["findings"]


def _gap_dicts(gaps) -> list[dict]:
    return [gap_to_dict(g) for g in gaps]


def test_evaluate_no_alias_equals_empty_alias() -> None:
    """Calling ``evaluate(findings)`` and ``evaluate(findings, alias_map={})``
    must produce byte-identical gap lists. The alias_map default arg is
    the documented greenfield contract — if it ever does anything other
    than no-op, the parity guarantee is broken."""
    findings = _load_findings("minimal_non_compliant.findings.json")

    no_alias = _gap_dicts(evaluate(findings))
    empty_alias = _gap_dicts(evaluate(findings, alias_map={}))

    assert no_alias == empty_alias


def test_evaluate_run_no_alias_file_equals_all_null_alias_file(tmp_path: Path) -> None:
    """Two real disk runs: one with no ``mg_alias.json``, one with the
    canonical all-null map. Expectation: identical ``gaps.json``."""
    findings_blob = json.dumps(
        {"findings": _load_findings("minimal_non_compliant.findings.json")}
    )

    # Run 1 — no alias file.
    run_a = tmp_path / "run_a"
    run_a.mkdir()
    findings_a = run_a / "findings.json"
    findings_a.write_text(findings_blob, encoding="utf-8")
    evaluate_run(findings_a, run_a / "gaps.json")
    gaps_a = json.loads((run_a / "gaps.json").read_text(encoding="utf-8"))

    # Run 2 — empty alias file (all roles → null).
    run_b = tmp_path / "run_b"
    run_b.mkdir()
    findings_b = run_b / "findings.json"
    findings_b.write_text(findings_blob, encoding="utf-8")
    (run_b / "mg_alias.json").write_text(
        json.dumps({role: None for role in CANONICAL_ROLES}, indent=2),
        encoding="utf-8",
    )
    evaluate_run(findings_b, run_b / "gaps.json")
    gaps_b = json.loads((run_b / "gaps.json").read_text(encoding="utf-8"))

    assert gaps_a == gaps_b


def test_discover_alias_loader_no_file_equals_all_null_file(tmp_path: Path) -> None:
    """Discover's alias loader treats ``no file`` and ``all-null file``
    identically — both yield an empty target list, leaving Discover's MG
    sweep at canonical SLZ names only (pre-v0.7.0 behaviour)."""
    no_file_dir = tmp_path / "no_file"
    no_file_dir.mkdir()

    null_file_dir = tmp_path / "all_null"
    null_file_dir.mkdir()
    (null_file_dir / "mg_alias.json").write_text(
        json.dumps({role: None for role in CANONICAL_ROLES}),
        encoding="utf-8",
    )

    assert load_aliased_mgs(no_file_dir / "findings.json") == []
    assert load_aliased_mgs(null_file_dir / "findings.json") == []
