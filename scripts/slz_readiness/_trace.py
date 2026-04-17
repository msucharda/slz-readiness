"""Trace writer — mandated by INSTRUCTIONS.md §7.

Every phase (discover, evaluate, scaffold) appends JSON lines to
`artifacts/<run>/trace.jsonl` so humans (and CI) can reconstruct exactly what
the pipeline did.

Usage from a CLI entry point::

    from slz_readiness._trace import tracer, log

    with tracer(run_dir, phase="discover"):
        ...  # any call into run_az / evaluate / scaffold emits lines here

Internal callers use ``log(event, **fields)``; when no tracer is active the
call becomes a cheap no-op so unit tests and library use don't need to set one
up.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class Tracer:
    """Appends newline-delimited JSON to ``<run_dir>/trace.jsonl``."""

    def __init__(self, run_dir: Path, phase: str) -> None:
        self.run_dir = run_dir
        self.phase = phase
        self.path = run_dir / "trace.jsonl"
        run_dir.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **fields: Any) -> None:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "phase": self.phase,
            "event": event,
        }
        for k, v in fields.items():
            record[k] = v
        line = json.dumps(record, sort_keys=True, default=str)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


_current: ContextVar[Tracer | None] = ContextVar("slz_tracer", default=None)


@contextmanager
def tracer(run_dir: Path | None, phase: str) -> Iterator[Tracer | None]:
    """Activate a tracer for the lifetime of the ``with`` block.

    ``run_dir=None`` is allowed for tests / library use — produces a no-op.
    Nesting is supported (restores the previous tracer on exit).
    """
    if run_dir is None or os.environ.get("SLZ_TRACE_DISABLE") == "1":
        yield None
        return
    active = Tracer(Path(run_dir), phase)
    token = _current.set(active)
    try:
        yield active
    finally:
        _current.reset(token)


def log(event: str, **fields: Any) -> None:
    """No-op if no tracer is active."""
    t = _current.get()
    if t is not None:
        t.log(event, **fields)


def is_active() -> bool:
    return _current.get() is not None
