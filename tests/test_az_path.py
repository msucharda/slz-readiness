"""Test that az_common resolves `az` via shutil.which for Windows compat."""
from __future__ import annotations

import importlib
import sys


def test_az_resolves_to_cmd_on_windows(monkeypatch):
    # Simulate Windows where `az` is on PATH as `az.cmd`.
    fake = r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"
    monkeypatch.setattr("shutil.which", lambda name: fake if name == "az" else None)

    # Force re-import so module-scope `_AZ` is recomputed.
    sys.modules.pop("slz_readiness.discover.az_common", None)
    mod = importlib.import_module("slz_readiness.discover.az_common")
    assert mod._AZ == fake  # noqa: SIM300


def test_az_falls_back_to_bare_name(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    sys.modules.pop("slz_readiness.discover.az_common", None)
    mod = importlib.import_module("slz_readiness.discover.az_common")
    assert mod._AZ == "az"  # noqa: SIM300
