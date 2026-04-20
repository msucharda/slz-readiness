"""Hook parity for v0.14.0 deploy-all scripts.

The scaffold phase may emit ``runbooks/deploy-all.{ps1,sh}`` + a companion
``grant-dine-roles.{ps1,sh}`` post-hook. The HITL contract (AGENTS.md §6/§7)
requires that the agent CANNOT execute these — the operator runs them.
This test pins the contract by asserting ``hooks/pre_tool_use.py`` blocks
every shape of invocation we expect an LLM might attempt.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "pre_tool_use.py"


def _load():
    spec = importlib.util.spec_from_file_location("pre_tool_use_hook", HOOK)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


hook = _load()


@pytest.mark.parametrize(
    "cmd",
    [
        "pwsh ./deploy-all.ps1 -Apply",
        "pwsh -File runbooks/deploy-all.ps1 -Apply",
        "bash ./deploy-all.sh --apply",
        "bash runbooks/deploy-all.sh --apply",
        "./runbooks/deploy-all.sh --apply",
        "./runbooks/grant-dine-roles.sh",
        "pwsh ./runbooks/grant-dine-roles.ps1",
        # Direct az deployment invocations inside the script
        "az deployment mg create --template-file foo.bicep",
        "az deployment sub create --template-file foo.bicep",
        "az role assignment create --assignee x --role y --scope /",
    ],
)
def test_agent_cannot_execute_emitted_scripts(cmd: str) -> None:
    """Every apply-path invocation is blocked by the pre-tool-use hook."""
    rc, reason = hook.decide(cmd)
    assert rc == 1, f"expected block for {cmd!r}, got rc={rc} reason={reason!r}"


@pytest.mark.parametrize(
    "cmd",
    [
        # What-if + read-only invocations pass the hook at the az layer.
        "az deployment mg what-if --template-file foo.bicep",
        "az deployment sub what-if --template-file foo.bicep",
        "az policy assignment list --scope /",
    ],
)
def test_whatif_and_read_only_still_pass(cmd: str) -> None:
    """Read-only az verbs (what-if, list, show) remain permitted."""
    rc, _ = hook.decide(cmd)
    assert rc == 0, f"expected pass for {cmd!r}"
