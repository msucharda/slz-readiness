"""Regression tests for slz-discover performance fixes.

Covers:
- run_az surfaces subprocess timeouts as AzError(kind=network)
- policy_assignments and identity_rbac only probe MGs that actually exist
- identity_rbac disables the expensive Graph-fill flags
- sovereignty_controls pushes compliance filter server-side
"""
from __future__ import annotations

import subprocess
from typing import Any

import pytest

from slz_readiness.discover import (
    az_common,
    identity_rbac,
    policy_assignments,
    sovereignty_controls,
)


def test_run_az_timeout_raises_network_azerror(monkeypatch):
    class FakeProc:
        def __init__(self, *args, **kwargs):
            self.pid = 12345
            self.returncode = None
            self.stdout = None
            self.stderr = None

        def communicate(self, timeout=None):
            # First call: simulate hang → raise TimeoutExpired.
            # Second call (after kill): return empty output.
            if not getattr(self, "_killed", False):
                raise subprocess.TimeoutExpired(cmd="az", timeout=timeout)
            return ("", "")

        def poll(self):
            return 0 if getattr(self, "_killed", False) else None

    created: dict[str, FakeProc] = {}

    def fake_popen(cmd, **kwargs):
        p = FakeProc()
        created["proc"] = p
        return p

    def fake_kill_tree(proc):
        proc._killed = True
        proc.returncode = -1

    monkeypatch.setattr(az_common.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(az_common, "_kill_tree", fake_kill_tree)

    with pytest.raises(az_common.AzError) as excinfo:
        az_common.run_az(["account", "list"])
    assert excinfo.value.kind == "network"
    assert "timeout" in str(excinfo.value).lower()


def test_slz_az_timeout_env_override(monkeypatch):
    captured: dict[str, Any] = {}

    class FakeProc:
        returncode = 0
        stdout = None
        stderr = None
        pid = 1

        def communicate(self, timeout=None):
            captured["timeout"] = timeout
            return ("[]", "")

        def poll(self):
            return 0

    monkeypatch.setenv("SLZ_AZ_TIMEOUT", "12.5")
    monkeypatch.setattr(az_common.subprocess, "Popen", lambda *a, **k: FakeProc())
    az_common.run_az(["account", "list"])
    assert captured["timeout"] == 12.5


def test_policy_assignments_skips_absent_mgs(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_az(args):
        calls.append(args)
        return []

    monkeypatch.setattr(policy_assignments, "run_az", fake_run_az)
    monkeypatch.setattr(
        "slz_readiness.discover.mg_hierarchy.present_mg_ids",
        lambda: ["slz", "platform"],
    )
    policy_assignments.discover()
    mg_scopes = [
        a[a.index("--scope") + 1].rsplit("/", 1)[-1]
        for a in calls
        if "--scope" in a
    ]
    assert set(mg_scopes) == {"slz", "platform"}


def test_identity_rbac_disables_graph_fills_and_intersects(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_az(args):
        calls.append(args)
        return []

    monkeypatch.setattr(identity_rbac, "run_az", fake_run_az)
    monkeypatch.setattr(
        "slz_readiness.discover.mg_hierarchy.present_mg_ids",
        lambda: ["slz"],
    )
    identity_rbac.discover()
    assert len(calls) == 1
    args = calls[0]
    # Graph-fill flags must be explicitly disabled.
    assert "--fill-principal-name" in args
    assert args[args.index("--fill-principal-name") + 1] == "false"
    assert "--fill-role-definition-name" in args
    assert args[args.index("--fill-role-definition-name") + 1] == "false"


def test_sovereignty_controls_uses_server_side_filter(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_az(args):
        calls.append(args)
        if args[:2] == ["account", "list"]:
            return [{"id": "sub-1"}]
        return []

    monkeypatch.setattr(sovereignty_controls, "run_az", fake_run_az)
    sovereignty_controls.discover()
    state_calls = [a for a in calls if a[:3] == ["policy", "state", "list"]]
    assert state_calls, "expected at least one policy state list call"
    for args in state_calls:
        assert "--query" not in args
        filter_val = args[args.index("--filter") + 1]
        assert "complianceState eq 'NonCompliant'" in filter_val
