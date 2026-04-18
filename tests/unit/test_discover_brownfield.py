"""Tests for v0.7.0 Discover brownfield alias loader."""
from __future__ import annotations

import json
from pathlib import Path

from slz_readiness.discover import _alias


def test_load_aliased_mgs_returns_only_non_null(tmp_path: Path) -> None:
    (tmp_path / "mg_alias.json").write_text(
        json.dumps(
            {
                "corp": "acme-prod-internal",
                "management": "acme-mgmt",
                "platform": None,
                "online": "  ",
                "connectivity": "acme-mgmt",
            }
        ),
        encoding="utf-8",
    )

    result = _alias.load_aliased_mgs(tmp_path / "findings.json")

    # Sorted, deduplicated, no nulls/whitespace.
    assert result == ["acme-mgmt", "acme-prod-internal"]


def test_load_aliased_mgs_missing_file_returns_empty(tmp_path: Path) -> None:
    """Greenfield-parity guarantee: no alias file → empty list →
    Discover sweeps only canonical SLZ MG names, byte-identical to v0.6.0."""
    assert _alias.load_aliased_mgs(tmp_path / "findings.json") == []


def test_load_aliased_mgs_malformed_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "mg_alias.json").write_text("not json", encoding="utf-8")
    assert _alias.load_aliased_mgs(tmp_path / "findings.json") == []


def test_load_aliased_mgs_top_level_not_dict_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "mg_alias.json").write_text(json.dumps(["corp", "platform"]), encoding="utf-8")
    assert _alias.load_aliased_mgs(tmp_path / "findings.json") == []


def test_set_resolve_run_dir_roundtrip(tmp_path: Path) -> None:
    _alias.set_run_dir(tmp_path)
    try:
        assert _alias.resolve_run_dir() == tmp_path
    finally:
        _alias.set_run_dir(None)
    assert _alias.resolve_run_dir() is None


def test_load_with_none_path(tmp_path: Path) -> None:
    """Loader must not crash when no path is supplied."""
    assert _alias.load_aliased_mgs(None) == []
