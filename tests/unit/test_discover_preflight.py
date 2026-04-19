"""Tests for required-extension preflight + AzError classifier."""
from __future__ import annotations

from click.testing import CliRunner
from slz_readiness.discover import cli
from slz_readiness.discover.az_common import _classify

# --- _classify --------------------------------------------------------------


def test_classify_missing_extension_requires_phrase():
    stderr = (
        "ERROR: The command requires the extension resource-graph. "
        "It will be installed first."
    )
    assert _classify(stderr, 2) == "missing_extension"


def test_classify_missing_extension_misspelled_phrase():
    stderr = (
        "ERROR: 'graph' is misspelled or not recognized by the system."
    )
    assert _classify(stderr, 2) == "missing_extension"


def test_classify_still_detects_permission_denied():
    assert _classify("AuthorizationFailed: ...", 1) == "permission_denied"


def test_classify_still_detects_not_found():
    assert _classify("ResourceNotFound: was not found", 1) == "not_found"


def test_classify_falls_back_to_network():
    assert _classify("something exploded", 1) == "network"


# --- preflight --------------------------------------------------------------


def _minimal_stubs():
    from tests.unit.test_discover_scope import _minimal_stubs as _m
    return _m()


def test_preflight_aborts_when_extension_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "DISCOVERERS", _minimal_stubs())
    monkeypatch.setattr(cli, "_resolve_active_tenant", lambda: "tenant-abc")
    # Override the conftest autouse stub with the real function so we exercise it.
    monkeypatch.setattr(
        cli, "run_az", lambda args: [] if args == ["extension", "list"] else []
    )
    monkeypatch.setattr(
        cli,
        "_check_required_extensions",
        cli._check_required_extensions_real,  # real impl preserved by conftest
    )

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
    assert "resource-graph" in result.output
    assert "az extension add" in result.output


def test_preflight_passes_when_extension_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "DISCOVERERS", _minimal_stubs())
    monkeypatch.setattr(cli, "_resolve_active_tenant", lambda: "tenant-abc")
    monkeypatch.setattr(
        cli,
        "run_az",
        lambda args: (
            [{"name": "resource-graph", "version": "2.1.1"}]
            if args == ["extension", "list"]
            else []
        ),
    )
    monkeypatch.setattr(
        cli, "_check_required_extensions", cli._check_required_extensions_real
    )
    monkeypatch.setattr(cli, "_list_tenant_subscriptions", lambda _tid: ["sub-a"])

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
    assert result.exit_code == 0, result.output
    assert "Missing required az CLI extension" not in result.output
