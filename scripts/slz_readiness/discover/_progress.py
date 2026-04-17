"""Lightweight progress reporting for discover stages.

Designed for two environments:

* **Interactive TTY** (developer running locally): one carriage-return-overwriting
  line per progress event, so the terminal doesn't fill with thousands of lines.
* **Non-TTY** (CI logs, captured stdout/stderr, the powershell tool): one line
  per "decile" boundary plus first/last so logs stay bounded but show real
  liveness.

Always writes to ``sys.stderr`` so the canonical ``findings.json`` summary on
``stdout`` stays uncontaminated for shell pipelines.
"""
from __future__ import annotations

import sys
from typing import Callable

ProgressCb = Callable[[str, int, int], None]


def _is_tty() -> bool:
    return bool(getattr(sys.stderr, "isatty", lambda: False)())


def make_callback(prefix: str) -> ProgressCb:
    """Return a progress callback bound to ``prefix``.

    The callback receives ``(label, i, n)`` where ``i`` is 1-based and ``n``
    is the total expected count. ``label`` is appended to ``prefix`` for
    display.
    """
    tty = _is_tty()
    last_decile = -1

    def cb(label: str, i: int, n: int) -> None:
        nonlocal last_decile
        if n <= 0:
            return
        if tty:
            line = f"  {prefix} [{i}/{n}] {label}"
            # Pad to clear any leftover characters from a longer previous line.
            sys.stderr.write("\r" + line.ljust(110)[:110])
            if i >= n:
                sys.stderr.write("\n")
            sys.stderr.flush()
            return
        # Non-TTY: emit at first, last, and decile boundaries only.
        decile = (i * 10) // max(n, 1)
        if i == 1 or i == n or decile != last_decile:
            last_decile = decile
            sys.stderr.write(f"  {prefix} [{i}/{n}] {label}\n")
            sys.stderr.flush()

    return cb


def noop_callback() -> ProgressCb:
    """Default callback for library use / tests — does nothing."""

    def cb(label: str, i: int, n: int) -> None:  # noqa: ARG001
        return

    return cb
