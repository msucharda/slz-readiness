"""Shared test fixtures for slz_readiness unit tests."""
from __future__ import annotations

import pytest
from slz_readiness.discover import cli as _discover_cli

# Preserve the real preflight implementation before any autouse stub runs so
# tests that exercise it (test_discover_preflight.py) can re-bind it.
_discover_cli._check_required_extensions_real = (  # type: ignore[attr-defined]
    _discover_cli._check_required_extensions
)


@pytest.fixture(autouse=True)
def _stub_required_extensions(monkeypatch):
    """Default: preflight finds every required extension.

    The preflight added in v0.7.x calls ``az extension list`` which, in tests,
    would hit the real CLI (or fail with FileNotFoundError when az is absent).
    Tests that specifically exercise the preflight override this fixture by
    re-monkeypatching ``cli._check_required_extensions``.
    """
    monkeypatch.setattr(_discover_cli, "_check_required_extensions", lambda: [])
