"""Shared brownfield alias loader for Discover (v0.7.0).

Discover's ``policy_assignments`` and ``identity_rbac`` modules sweep a fixed
list of canonical SLZ MG names. On a brownfield tenant the operator's
``slz-reconcile`` step writes ``artifacts/<run>/mg_alias.json`` mapping
canonical roles to the operator's actual MG names. This loader reads that
file (when it exists) so Discover can sweep the union of canonical names
AND aliased customer MG names — without which Evaluate's selector-rewrite
finds no observed data to match against.

Greenfield-parity contract: missing or empty alias map → returns empty list.
A Discover run with no alias file produces byte-identical findings to
v0.6.0.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

from .. import _trace

_ALIAS_FILE = "mg_alias.json"


def _candidate_dirs(out_path: Path | None) -> list[Path]:
    """Locations to probe for ``mg_alias.json``.

    Discover's CLI only knows ``--out findings.json``, so the alias file
    (written by ``slz-reconcile``) lives next to ``findings.json`` in the
    same run directory. We accept either an explicit dir or a findings
    path and fall back to its parent.
    """
    if out_path is None:
        return []
    if out_path.is_dir():
        return [out_path]
    return [out_path.parent]


def load_aliased_mgs(out_path: Path | None) -> list[str]:
    """Return the deduplicated list of non-null customer MG names from
    ``mg_alias.json`` adjacent to the run's ``findings.json``.

    Empty list when the file is absent, malformed, or contains only nulls.
    The loader never raises — a botched alias file must not block Discover;
    schema validation belongs in :mod:`slz_readiness.reconcile.schema` and
    the ``hooks/post_tool_use.py`` alias guard.
    """
    seen: set[str] = set()
    out: list[str] = []
    for d in _candidate_dirs(out_path):
        path = d / _ALIAS_FILE
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _trace.log("discover.alias.skip", reason=f"{type(exc).__name__}: {exc}")
            continue
        if not isinstance(raw, dict):
            _trace.log("discover.alias.skip", reason="top-level not a dict")
            continue
        for role, value in raw.items():
            if not isinstance(role, str) or not isinstance(value, str):
                continue
            stripped = value.strip()
            if not stripped or stripped in seen:
                continue
            seen.add(stripped)
            out.append(stripped)
    if out:
        _trace.log("discover.alias.loaded", aliased_mg_count=len(out))
    return sorted(out)


# v0.7.1: legacy module-global state retained as deprecated thin shims so
# external callers (and the unit test that exercises the round-trip) keep
# working. New code MUST pass ``run_dir`` explicitly to ``load_aliased_mgs``;
# discoverer modules now accept a ``run_dir`` kwarg from the CLI.
_RUN_DIR: Path | None = None


def set_run_dir(path: Path | None) -> None:
    """Deprecated: prefer passing ``run_dir`` explicitly to discoverers."""
    warnings.warn(
        "set_run_dir is deprecated; pass run_dir explicitly to discover()",
        DeprecationWarning,
        stacklevel=2,
    )
    global _RUN_DIR
    _RUN_DIR = path


def resolve_run_dir() -> Path | None:
    """Deprecated: prefer the ``run_dir`` parameter on discoverers."""
    return _RUN_DIR

