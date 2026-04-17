"""Tests for per-stage progress feedback in slz-discover."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from slz_readiness.discover import (
    _progress,
    cli,
    identity_rbac,
    logging_monitoring,
    mg_hierarchy,
    policy_assignments,
    sovereignty_controls,
    subscription_inventory,
)


def _stub(name: str, findings: list):
    class M:
        @staticmethod
        def discover(progress_cb=None, subscription_filter=None):  # noqa: ARG004
            return findings

    M.__name__ = f"slz_readiness.discover.{name}"
    return M


def _mock_active_tenant(monkeypatch, tenant_id: str = "tenant-abc"):
    """Stub `az account show` so CLI scope-validation passes."""
    monkeypatch.setattr(
        cli, "_resolve_active_tenant", lambda: tenant_id
    )
    monkeypatch.setattr(
        cli, "_list_tenant_subscriptions", lambda tid: ["sub-a", "sub-b"]
    )


def test_cli_emits_stage_lines_and_writes_partials(monkeypatch, tmp_path):
    stubs = [
        _stub("mg_hierarchy", [{"a": 1}]),
        _stub("subscription_inventory", [{"b": 2}, {"b": 3}]),
        _stub("policy_assignments", []),
        _stub("identity_rbac", []),
        _stub("logging_monitoring", [{"c": 4}]),
        _stub("sovereignty_controls", []),
    ]
    monkeypatch.setattr(cli, "DISCOVERERS", stubs)
    _mock_active_tenant(monkeypatch)

    out = tmp_path / "findings.json"
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["--out", str(out), "--tenant", "tenant-abc", "--all-subscriptions"],
    )
    assert result.exit_code == 0, (result.output or "") + (result.stderr or "")

    # Stage lines on stderr.
    stderr = result.stderr
    assert "▶ mg_hierarchy" in stderr
    assert "✓ mg_hierarchy — 1 findings" in stderr
    assert "✓ subscription_inventory — 2 findings" in stderr
    assert "✓ logging_monitoring — 1 findings" in stderr

    # Final summary on stdout.
    assert f"Wrote 4 findings -> {out}" in result.stdout

    # Partials.
    stages = tmp_path / "stages"
    assert (stages / "mg_hierarchy.json").exists()
    assert (stages / "subscription_inventory.json").exists()
    assert (stages / "policy_assignments.json").exists()
    mg_partial = json.loads((stages / "mg_hierarchy.json").read_text())
    assert mg_partial == {"findings": [{"a": 1}]}

    # Final findings.json equals union of partials.
    final = json.loads(out.read_text())
    union = []
    for stub in stubs:
        name = stub.__name__.rsplit(".", 1)[-1]
        union.extend(json.loads((stages / f"{name}.json").read_text())["findings"])
    assert final["findings"] == union


def test_cli_records_stage_error_without_aborting(monkeypatch, tmp_path):
    class Boom:
        @staticmethod
        def discover(progress_cb=None, subscription_filter=None):  # noqa: ARG004
            raise RuntimeError("kaboom")

    Boom.__name__ = "slz_readiness.discover.boom"

    stubs = [_stub("mg_hierarchy", [{"a": 1}]), Boom, _stub("logging_monitoring", [])]
    monkeypatch.setattr(cli, "DISCOVERERS", stubs)
    _mock_active_tenant(monkeypatch)

    out = tmp_path / "findings.json"
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["--out", str(out), "--tenant", "tenant-abc", "--all-subscriptions"],
    )
    assert result.exit_code == 0
    assert "✗ boom — error" in result.stderr
    assert "kaboom" in result.stderr
    # Other stages still ran and final file exists.
    assert json.loads(out.read_text())["findings"] == [{"a": 1}]


def test_progress_helper_non_tty_emits_decile_boundaries(monkeypatch):
    buf = io.StringIO()
    buf.isatty = lambda: False  # type: ignore[assignment]
    monkeypatch.setattr(sys, "stderr", buf)
    cb = _progress.make_callback("test")
    n = 100
    for i in range(1, n + 1):
        cb(f"item-{i}", i, n)
    lines = [line for line in buf.getvalue().splitlines() if line.strip()]
    # Expect ~11 lines (i=1, then deciles 1..10 transitions, with i=n forced).
    assert "\r" not in buf.getvalue()
    assert 8 <= len(lines) <= 13, f"got {len(lines)} lines: {lines}"
    assert any("[1/100]" in l for l in lines)
    assert any("[100/100]" in l for l in lines)


def test_progress_helper_tty_uses_carriage_return(monkeypatch):
    buf = io.StringIO()
    buf.isatty = lambda: True  # type: ignore[assignment]
    monkeypatch.setattr(sys, "stderr", buf)
    cb = _progress.make_callback("test")
    cb("a", 1, 3)
    cb("b", 2, 3)
    cb("c", 3, 3)
    out = buf.getvalue()
    assert out.count("\r") == 3
    # Final write ends with newline.
    assert out.endswith("\n")


def test_sovereignty_controls_invokes_progress_per_call(monkeypatch):
    calls: list[tuple[str, int, int]] = []

    def fake_run_az(args):
        if args[:2] == ["account", "list"]:
            return [{"id": "sub-1"}, {"id": "sub-2"}]
        return []

    monkeypatch.setattr(sovereignty_controls, "run_az", fake_run_az)
    sovereignty_controls.discover(progress_cb=lambda label, i, n: calls.append((label, i, n)))
    # 2 subs × 2 assignments = 4 progress calls.
    assert len(calls) == 4
    assert [c[1] for c in calls] == [1, 2, 3, 4]
    assert all(c[2] == 4 for c in calls)


def test_policy_assignments_invokes_progress_per_mg(monkeypatch):
    calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr(policy_assignments, "run_az", lambda args: [])
    monkeypatch.setattr(
        "slz_readiness.discover.mg_hierarchy.present_mg_ids",
        lambda: ["slz", "platform", "corp"],
    )
    policy_assignments.discover(progress_cb=lambda label, i, n: calls.append((label, i, n)))
    assert [c[1] for c in calls] == [1, 2, 3]
    assert all(c[2] == 3 for c in calls)


def test_modules_remain_callable_without_progress_cb(monkeypatch):
    # Backward compat: tests that call discover() with no kwargs must still work.
    monkeypatch.setattr(policy_assignments, "run_az", lambda args: [])
    monkeypatch.setattr(identity_rbac, "run_az", lambda args: [])
    monkeypatch.setattr(sovereignty_controls, "run_az", lambda args: [])
    monkeypatch.setattr(
        "slz_readiness.discover.mg_hierarchy.present_mg_ids", lambda: []
    )
    assert policy_assignments.discover() == []
    assert identity_rbac.discover() == []
    assert sovereignty_controls.discover() == []
