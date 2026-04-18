"""Tests for the shared ``mg_alias.json`` loader (v0.7.1)."""
from __future__ import annotations

import json
from pathlib import Path

from slz_readiness._alias_io import load_alias_map


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_returns_only_non_null_string_entries(tmp_path: Path) -> None:
    _write(tmp_path / "mg_alias.json", {
        "corp": "acme-corp",
        "platform": None,
        "online": "  ",
        "management": "acme-mgmt",
    })
    out = load_alias_map(tmp_path)
    assert out == {"corp": "acme-corp", "management": "acme-mgmt"}


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_alias_map(tmp_path) == {}


def test_none_run_dir_returns_empty() -> None:
    assert load_alias_map(None) == {}


def test_malformed_json_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "mg_alias.json").write_text("not json", encoding="utf-8")
    assert load_alias_map(tmp_path) == {}


def test_top_level_not_dict_returns_empty(tmp_path: Path) -> None:
    _write(tmp_path / "mg_alias.json", ["corp", "platform"])
    assert load_alias_map(tmp_path) == {}


def test_strips_whitespace_around_values(tmp_path: Path) -> None:
    _write(tmp_path / "mg_alias.json", {"corp": "  acme-corp  "})
    assert load_alias_map(tmp_path) == {"corp": "acme-corp"}
