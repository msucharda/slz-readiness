"""Shared I/O loader for ``mg_alias.json`` (v0.7.1).

Three call sites previously each implemented near-identical alias-loading
logic:

* :func:`slz_readiness.evaluate.engine._load_alias_map`
* :func:`slz_readiness.scaffold.engine._load_alias_map`
* :func:`slz_readiness.scaffold.cli._load_alias_for_doc`

…with subtly different trace-event names and slightly different shapes
(some return ``dict[str, str]`` of non-null entries, some also wanted the
list-of-customer-MGs view used by Discover). This module unifies them.

Contract:

* ``load_alias_map(run_dir, trace_label)`` → ``{role: customer_mg}`` for
  every non-null entry. Empty dict when file is missing, malformed,
  unparseable, or top-level is not a dict. Never raises.
* ``trace_label`` selects the prefix used for ``_trace.log`` events
  (e.g. ``"evaluate"`` → ``"evaluate.alias.skip"`` /
  ``"evaluate.alias.loaded"``). Pass ``None`` to silence tracing.

Call sites add their own conventions on top (e.g. Discover wants the
sorted, de-duplicated list; that lives in ``discover/_alias.py`` which
calls this loader and post-processes).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import _trace

_ALIAS_FILE = "mg_alias.json"


def load_alias_map(run_dir: Path | None, trace_label: str | None = None) -> dict[str, str]:
    """Return ``{role: customer_mg}`` for every non-null entry in
    ``run_dir/mg_alias.json``. See module docstring for full contract.
    """
    if run_dir is None:
        return {}
    path = run_dir / _ALIAS_FILE
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if trace_label:
            _trace.log(f"{trace_label}.alias.skip", reason=f"{type(exc).__name__}: {exc}")
        return {}
    if not isinstance(raw, dict):
        if trace_label:
            _trace.log(f"{trace_label}.alias.skip", reason="top-level not a dict")
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, str) and v.strip():
            out[k] = v.strip()
    if out and trace_label:
        _trace.log(f"{trace_label}.alias.loaded", mapped=len(out))
    return out
