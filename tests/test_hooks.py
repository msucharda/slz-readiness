"""Tests for the Python pre_tool_use hook."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "pre_tool_use.py"


def _load():
    spec = importlib.util.spec_from_file_location("pre_tool_use_hook", HOOK)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


hook = _load()


@pytest.mark.parametrize(
    "cmd, expected_rc",
    [
        ("az account list", 0),
        ("az policy assignment list --scope /", 0),
        ("az graph query --graph-query resources", 0),
        ("az group create --name foo --location eastus", 1),
        ("az account set --subscription 00000000-0000-0000-0000-000000000000", 1),
        ("bicep build main.bicep", 1),  # "build" isn't in the allowlist
        ("git log --oneline", 0),       # non-az passes through
        ("", 0),                         # empty command passes through
    ],
)
def test_decide(cmd, expected_rc):
    rc, _ = hook.decide(cmd)
    assert rc == expected_rc


def test_extract_command_command_field():
    assert hook.extract_command({"command": "az account show"}) == "az account show"


def test_extract_command_tool_args():
    payload = {"tool": "az", "args": ["account", "list"]}
    assert hook.extract_command(payload) == "az account list"


def test_main_malformed_json_passes(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not valid json"))
    assert hook.main() == 0


def test_main_empty_stdin_passes(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert hook.main() == 0


def test_main_deny_returns_1(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"command": "az group delete --name x"}))
    )
    assert hook.main() == 1
    err = capsys.readouterr().err
    assert "BLOCKED" in err


def test_main_allow_returns_0(monkeypatch):
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"command": "az account show"}))
    )
    assert hook.main() == 0
