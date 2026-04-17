"""Tests for tenant / subscription scope confirmation in slz-discover."""
from __future__ import annotations

import json

from click.testing import CliRunner

from slz_readiness.discover import (
    cli,
    logging_monitoring,
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


def _minimal_stubs():
    return [
        _stub("mg_hierarchy", []),
        _stub("subscription_inventory", []),
        _stub("policy_assignments", []),
        _stub("identity_rbac", []),
        _stub("logging_monitoring", []),
        _stub("sovereignty_controls", []),
    ]


# --- CLI flag validation ------------------------------------------------

def test_cli_requires_tenant_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "DISCOVERERS", _minimal_stubs())
    out = tmp_path / "findings.json"
    runner = CliRunner()
    result = runner.invoke(cli.main, ["--out", str(out), "--all-subscriptions"])
    assert result.exit_code != 0
    assert "Missing option" in result.output or "--tenant" in result.output


def test_cli_requires_explicit_scope(monkeypatch, tmp_path):
    """Neither --subscription nor --all-subscriptions must fail fast."""
    monkeypatch.setattr(cli, "DISCOVERERS", _minimal_stubs())
    monkeypatch.setattr(cli, "_resolve_active_tenant", lambda: "tenant-abc")
    out = tmp_path / "findings.json"
    runner = CliRunner()
    result = runner.invoke(cli.main, ["--out", str(out), "--tenant", "tenant-abc"])
    assert result.exit_code != 0
    assert "scope" in result.output.lower() or "--all-subscriptions" in result.output


def test_cli_rejects_subscription_and_all_together(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "DISCOVERERS", _minimal_stubs())
    monkeypatch.setattr(cli, "_resolve_active_tenant", lambda: "tenant-abc")
    out = tmp_path / "findings.json"
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "--out", str(out),
            "--tenant", "tenant-abc",
            "--subscription", "sub-a",
            "--all-subscriptions",
        ],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output.lower()


def test_cli_rejects_tenant_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "DISCOVERERS", _minimal_stubs())
    monkeypatch.setattr(cli, "_resolve_active_tenant", lambda: "tenant-active")
    out = tmp_path / "findings.json"
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "--out", str(out),
            "--tenant", "tenant-other",
            "--all-subscriptions",
        ],
    )
    assert result.exit_code != 0
    assert "tenant-active" in result.output
    assert "az login" in result.output


def test_cli_requires_active_az_session(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "DISCOVERERS", _minimal_stubs())
    monkeypatch.setattr(cli, "_resolve_active_tenant", lambda: None)
    out = tmp_path / "findings.json"
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "--out", str(out),
            "--tenant", "tenant-abc",
            "--all-subscriptions",
        ],
    )
    assert result.exit_code != 0
    assert "az login" in result.output


# --- run_scope persistence ----------------------------------------------

def test_cli_writes_run_scope_filtered(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "DISCOVERERS", _minimal_stubs())
    monkeypatch.setattr(cli, "_resolve_active_tenant", lambda: "tenant-abc")
    out = tmp_path / "findings.json"
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "--out", str(out),
            "--tenant", "tenant-abc",
            "--subscription", "sub-a",
            "--subscription", "sub-b",
        ],
    )
    assert result.exit_code == 0, result.output + (result.stderr or "")
    data = json.loads(out.read_text())
    assert data["run_scope"] == {
        "tenant_id": "tenant-abc",
        "mode": "filtered",
        "subscription_ids": ["sub-a", "sub-b"],
    }


def test_cli_writes_run_scope_all(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "DISCOVERERS", _minimal_stubs())
    monkeypatch.setattr(cli, "_resolve_active_tenant", lambda: "tenant-abc")
    monkeypatch.setattr(
        cli, "_list_tenant_subscriptions", lambda tid: ["sub-x", "sub-y", "sub-z"]
    )
    out = tmp_path / "findings.json"
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["--out", str(out), "--tenant", "tenant-abc", "--all-subscriptions"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text())
    assert data["run_scope"]["mode"] == "all"
    assert data["run_scope"]["subscription_ids"] == ["sub-x", "sub-y", "sub-z"]


# --- filter propagation --------------------------------------------------

def test_cli_passes_subscription_filter_to_discoverers(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    class Recorder:
        @staticmethod
        def discover(progress_cb=None, subscription_filter=None):  # noqa: ARG004
            seen["filter"] = subscription_filter
            return []

    Recorder.__name__ = "slz_readiness.discover.sovereignty_controls"
    monkeypatch.setattr(cli, "DISCOVERERS", [Recorder])
    monkeypatch.setattr(cli, "_resolve_active_tenant", lambda: "tenant-abc")
    out = tmp_path / "findings.json"
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "--out", str(out),
            "--tenant", "tenant-abc",
            "--subscription", "sub-a",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen["filter"] == {"sub-a"}


def test_cli_all_subscriptions_passes_tenant_filter(monkeypatch, tmp_path):
    """--all-subscriptions must pin the filter to the tenant-scoped sub set,
    so discoverers calling `az account list --all` (cross-tenant) do not
    silently fan out beyond the confirmed tenant."""
    seen: dict[str, object] = {}

    class Recorder:
        @staticmethod
        def discover(progress_cb=None, subscription_filter=None):  # noqa: ARG004
            seen["filter"] = subscription_filter
            return []

    Recorder.__name__ = "slz_readiness.discover.sovereignty_controls"
    monkeypatch.setattr(cli, "DISCOVERERS", [Recorder])
    monkeypatch.setattr(cli, "_resolve_active_tenant", lambda: "tenant-abc")
    monkeypatch.setattr(
        cli, "_list_tenant_subscriptions", lambda tid: ["sub-x", "sub-y", "sub-z"]
    )
    out = tmp_path / "findings.json"
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["--out", str(out), "--tenant", "tenant-abc", "--all-subscriptions"],
    )
    assert result.exit_code == 0, result.output
    assert seen["filter"] == {"sub-x", "sub-y", "sub-z"}


def test_cli_all_subscriptions_empty_tenant_passes_none(monkeypatch, tmp_path):
    """Pathological edge: tenant with zero subs keeps filter=None so
    discoverers still emit tenant-level error findings."""
    seen: dict[str, object] = {}

    class Recorder:
        @staticmethod
        def discover(progress_cb=None, subscription_filter=None):  # noqa: ARG004
            seen["filter"] = subscription_filter
            return []

    Recorder.__name__ = "slz_readiness.discover.sovereignty_controls"
    monkeypatch.setattr(cli, "DISCOVERERS", [Recorder])
    monkeypatch.setattr(cli, "_resolve_active_tenant", lambda: "tenant-abc")
    monkeypatch.setattr(cli, "_list_tenant_subscriptions", lambda tid: [])
    out = tmp_path / "findings.json"
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["--out", str(out), "--tenant", "tenant-abc", "--all-subscriptions"],
    )
    assert result.exit_code == 0, result.output
    assert seen["filter"] is None


# --- Module-level: filter actually narrows work -------------------------

def test_sovereignty_controls_honours_filter(monkeypatch):
    """With a 3-sub tenant but a 1-sub filter, only 2 policy-state calls fire."""
    calls: list[list[str]] = []

    def fake_run_az(args):
        calls.append(args)
        if args[:2] == ["account", "list"]:
            return [{"id": "sub-a"}, {"id": "sub-b"}, {"id": "sub-c"}]
        return []

    monkeypatch.setattr(sovereignty_controls, "run_az", fake_run_az)
    sovereignty_controls.discover(subscription_filter={"sub-b"})
    # account list + 2 policy-state calls (one per assignment), only for sub-b.
    policy_calls = [c for c in calls if c and c[0] == "policy"]
    assert len(policy_calls) == 2
    for c in policy_calls:
        assert "sub-b" in c


def test_sovereignty_controls_ignores_cross_tenant_subs(monkeypatch):
    """Regression: `az account list --all` returns subs across every tenant
    the user is a guest in. When the CLI supplies a tenant-scoped filter,
    sovereignty_controls must NOT issue policy-state calls for foreign-tenant
    subs — that was the source of the 10-vs-164 fan-out bug."""
    calls: list[list[str]] = []

    def fake_run_az(args):
        calls.append(args)
        if args[:2] == ["account", "list"]:
            # Two tenants visible; only tenant-A is in scope.
            return [
                {"id": "sub-a1", "tenantId": "tenant-A"},
                {"id": "sub-a2", "tenantId": "tenant-A"},
                {"id": "sub-b1", "tenantId": "tenant-B"},
                {"id": "sub-b2", "tenantId": "tenant-B"},
            ]
        return []

    monkeypatch.setattr(sovereignty_controls, "run_az", fake_run_az)
    sovereignty_controls.discover(subscription_filter={"sub-a1", "sub-a2"})
    policy_calls = [c for c in calls if c and c[0] == "policy"]
    # 2 in-scope subs × 2 assignments = 4; no calls for sub-b1 / sub-b2.
    assert len(policy_calls) == 4
    touched = {c[c.index("--subscription") + 1] for c in policy_calls}
    assert touched == {"sub-a1", "sub-a2"}


def test_subscription_inventory_honours_filter(monkeypatch):
    def fake_run_az(args):  # noqa: ARG001
        return [
            {"id": "sub-a", "name": "A", "tenantId": "t", "state": "Enabled"},
            {"id": "sub-b", "name": "B", "tenantId": "t", "state": "Enabled"},
            {"id": "sub-c", "name": "C", "tenantId": "t", "state": "Enabled"},
        ]

    monkeypatch.setattr(subscription_inventory, "run_az", fake_run_az)
    findings = subscription_inventory.discover(subscription_filter={"sub-b"})
    assert len(findings) == 1
    assert findings[0]["scope"] == "subscription/sub-b"


def test_logging_monitoring_uses_subscriptions_flag(monkeypatch):
    captured: list[list[str]] = []

    def fake_run_az(args):
        captured.append(list(args))
        return {"data": []}

    monkeypatch.setattr(logging_monitoring, "run_az", fake_run_az)
    logging_monitoring.discover(subscription_filter={"sub-a", "sub-b"})
    assert len(captured) == 1
    args = captured[0]
    assert "--subscriptions" in args
    # Idx of --subscriptions and subsequent ids.
    idx = args.index("--subscriptions")
    tail = args[idx + 1:]
    assert "sub-a" in tail and "sub-b" in tail


def test_logging_monitoring_omits_flag_when_no_filter(monkeypatch):
    captured: list[list[str]] = []

    def fake_run_az(args):
        captured.append(list(args))
        return {"data": []}

    monkeypatch.setattr(logging_monitoring, "run_az", fake_run_az)
    logging_monitoring.discover()
    assert "--subscriptions" not in captured[0]


# --- Back-compat ---------------------------------------------------------

def test_modules_accept_no_kwargs():
    """Discoverers must remain callable with bare discover() for test fixtures."""
    # These raise AzError-style failures inside — caught via run_az stubs in
    # sibling tests. Here we only confirm the signatures still have defaults.
    import inspect
    for mod in (subscription_inventory, sovereignty_controls, logging_monitoring):
        sig = inspect.signature(mod.discover)
        for p in sig.parameters.values():
            assert p.default is not inspect.Parameter.empty, (
                f"{mod.__name__}.discover parameter {p.name} has no default"
            )
