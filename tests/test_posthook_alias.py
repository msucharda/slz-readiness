"""Tests for the mg_alias.json branch of the post_tool_use hook."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "post_tool_use.py"


def _load():
    spec = importlib.util.spec_from_file_location("post_tool_use_hook", HOOK)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


hook = _load()


def _write_findings(run_dir: Path, present_ids: list[str]) -> None:
    """Write a minimal findings.json with the MG-summary record the
    hook expects to find ``present_ids`` inside."""
    findings = {
        "findings": [
            {
                "resource_type": "microsoft.management/managementgroups.summary",
                "observed_state": {"present_ids": present_ids},
            }
        ]
    }
    (run_dir / "findings.json").write_text(json.dumps(findings), encoding="utf-8")


def test_unknown_alias_is_nulled(tmp_path: Path) -> None:
    _write_findings(tmp_path, ["acme-prod-internal", "acme-management"])
    alias_path = tmp_path / "mg_alias.json"
    alias_path.write_text(
        json.dumps(
            {
                "corp": "acme-prod-internal",
                "management": "definitely-not-a-real-mg",
                "platform": None,
            }
        ),
        encoding="utf-8",
    )

    nulled = hook._filter_alias(alias_path)

    assert nulled == 1
    rewritten = json.loads(alias_path.read_text(encoding="utf-8"))
    assert rewritten["corp"] == "acme-prod-internal"
    assert rewritten["management"] is None
    assert rewritten["platform"] is None
    drop_path = alias_path.with_suffix(".dropped.md")
    assert drop_path.is_file()
    assert "definitely-not-a-real-mg" in drop_path.read_text(encoding="utf-8")


def test_all_known_aliases_pass_through(tmp_path: Path) -> None:
    _write_findings(tmp_path, ["acme-prod", "acme-mgmt"])
    alias_path = tmp_path / "mg_alias.json"
    original = json.dumps(
        {"corp": "acme-prod", "management": "acme-mgmt", "platform": None},
        sort_keys=True,
    )
    alias_path.write_text(original, encoding="utf-8")

    nulled = hook._filter_alias(alias_path)

    assert nulled == 0
    # File untouched on the happy path; no .dropped.md emitted.
    assert alias_path.read_text(encoding="utf-8") == original
    assert not alias_path.with_suffix(".dropped.md").is_file()


def test_missing_findings_skips_guard(tmp_path: Path) -> None:
    """No sibling findings.json → the hook can't validate, so it leaves
    the alias file untouched (silent skip, never block)."""
    alias_path = tmp_path / "mg_alias.json"
    original = json.dumps({"corp": "anything-goes-here"}, sort_keys=True)
    alias_path.write_text(original, encoding="utf-8")

    nulled = hook._filter_alias(alias_path)

    assert nulled == 0
    assert alias_path.read_text(encoding="utf-8") == original
    assert not alias_path.with_suffix(".dropped.md").is_file()


def test_extract_alias_path_only_matches_basename(tmp_path: Path) -> None:
    f = tmp_path / "mg_alias.json"
    f.write_text("{}", encoding="utf-8")
    assert hook._extract_alias_path({"path": str(f)}) == f
    assert hook._extract_alias_path({"path": str(tmp_path / "other.json")}) is None
    assert hook._extract_alias_path({}) is None
