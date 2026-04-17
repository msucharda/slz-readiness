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


# --- post_tool_use.py ----------------------------------------------------

POST_HOOK = Path(__file__).resolve().parents[1] / "hooks" / "post_tool_use.py"


def _load_post():
    spec = importlib.util.spec_from_file_location("post_tool_use_hook", POST_HOOK)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


post_hook = _load_post()


def test_plan_summary_not_filtered(tmp_path):
    """The citation guard must NOT match plan.summary.md - it only targets plan.md."""
    p = tmp_path / "plan.summary.md"
    p.write_text("- bullet with no citation\n", encoding="utf-8")
    assert post_hook._extract_plan_path({"output_path": str(p)}) is None


def test_plan_md_still_matches(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text("- bullet\n", encoding="utf-8")
    assert post_hook._extract_plan_path({"output_path": str(p)}) == p


# --- Fix 7: transport-layer guard ----------------------------------------
# Regression tests: the az/azd/bicep gate must not be bypassable by raw HTTP
# clients or az rest. See research report issue #7.


@pytest.mark.parametrize(
    "cmd",
    [
        # Invoke-RestMethod PUT against ARM control plane
        'Invoke-RestMethod -Method Put -Uri "https://management.azure.com/subscriptions/x?api-version=2020-01-01"',
        'Invoke-RestMethod -Uri "https://management.azure.com/x" -Method Post -Body $b',
        'irm -Method Delete "https://management.azure.com/x"',
        # curl write against ARM
        'curl -X PUT "https://management.azure.com/subscriptions/x?api-version=2020-01-01" '
        '-d @body.json',
        'curl.exe -X POST https://management.azure.com/x',
        # wget write against Graph
        'wget --method=PATCH https://graph.microsoft.com/v1.0/directoryRoles',
        # az rest writes
        "az rest --method put --url https://management.azure.com/x",
        "az rest --method POST --url https://management.azure.com/x",
        "az rest --method delete --url https://management.azure.com/x",
        "az rest --method patch --url https://management.azure.com/x",
    ],
)
def test_transport_guard_blocks_azure_writes(cmd):
    rc, msg = hook.decide(cmd)
    assert rc == 1, f"expected BLOCK for: {cmd}"
    assert "BLOCKED" in msg


@pytest.mark.parametrize(
    "cmd",
    [
        # read-only Invoke-RestMethod / curl (no write method)
        'Invoke-RestMethod -Uri "https://management.azure.com/subscriptions?api-version=2020-01-01"',
        'curl https://management.azure.com/subscriptions?api-version=2020-01-01',
        # write method, but non-Azure host
        'Invoke-RestMethod -Method Put -Uri "https://example.com/api"',
        'curl -X POST https://api.github.com/repos/foo/bar/issues',
    ],
)
def test_transport_guard_allows_reads_and_non_azure(cmd):
    rc, _ = hook.decide(cmd)
    assert rc == 0, f"expected ALLOW for: {cmd}"
