"""Shared formatting primitives for human-readable phase summaries.

Each phase (Discover, Evaluate, Plan, Scaffold) writes a pair of artifacts:
``<phase>.summary.json`` (machine-readable) and ``<phase>.summary.md``
(human-readable). Both are deterministic by construction — they contain no
timestamps outside the header line and never depend on iteration order of
unsorted collections. The helpers here keep that discipline in one place.

ASCII-only (no Unicode box-drawing) to keep the three-OS CI matrix happy. The
``Successful``/``Failed`` glyphs match Discover's existing ``checkmark``/``cross``
convention.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ASCII glyphs matching discover/cli.py output.
GLYPH_OK = "[OK]"
GLYPH_FAIL = "[FAIL]"
GLYPH_WARN = "[WARN]"

# Canonical severity order (used for stable rendering of tally tables).
SEVERITY_ORDER: tuple[str, ...] = ("critical", "high", "medium", "low", "info", "unknown")

# Canonical gap-status order.
STATUS_ORDER: tuple[str, ...] = ("missing", "misconfigured", "unknown")


# -----------------------------------------------------------------------------
# Tally / extraction helpers
# -----------------------------------------------------------------------------

def severity_tally(gaps: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Count gaps by severity. Always returns every canonical key (0 when absent)."""
    counts = Counter((g.get("severity") or "unknown") for g in gaps)
    out = {k: counts.get(k, 0) for k in SEVERITY_ORDER}
    # Preserve any unexpected severities at the tail, sorted for determinism.
    extras = sorted(k for k in counts if k not in out)
    for k in extras:
        out[k] = counts[k]
    return out


def design_area_tally(gaps: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Count gaps by design_area. Sorted by area name for determinism."""
    counts = Counter((g.get("design_area") or "unknown") for g in gaps)
    return {k: counts[k] for k in sorted(counts)}


def status_tally(gaps: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Count gaps by status. Always returns every canonical key (0 when absent)."""
    counts = Counter((g.get("status") or "missing") for g in gaps)
    out = {k: counts.get(k, 0) for k in STATUS_ORDER}
    extras = sorted(k for k in counts if k not in out)
    for k in extras:
        out[k] = counts[k]
    return out


def unknown_gaps(gaps: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return gaps with ``status == 'unknown'`` as plain dicts, sorted stably."""
    unknowns = [dict(g) for g in gaps if g.get("status") == "unknown"]
    unknowns.sort(key=lambda g: (g.get("rule_id", ""), g.get("resource_id", "")))
    return unknowns


def error_findings(findings: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return the subset of findings whose observed_state carries an ``error``."""
    out: list[dict[str, Any]] = []
    for f in findings:
        obs = f.get("observed_state")
        if isinstance(obs, dict) and "error" in obs:
            out.append(dict(f))
    out.sort(key=lambda f: (f.get("resource_type", ""), f.get("resource_id", "")))
    return out


# -----------------------------------------------------------------------------
# Rendering helpers
# -----------------------------------------------------------------------------

def utc_now_iso() -> str:
    """ISO-8601 UTC timestamp to millisecond precision, ``Z`` suffix."""
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def render_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """Render a pipe-separated markdown table with a header separator row.

    Empty ``rows`` produces just the header + separator so the table shape is
    still visible in the rendered markdown.
    """
    cells = [[str(c) for c in row] for row in rows]
    head = list(headers)
    widths = [len(h) for h in head]
    for row in cells:
        for i, val in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(val))
    def _fmt(row: Sequence[str]) -> str:
        padded = [row[i].ljust(widths[i]) if i < len(widths) else row[i] for i in range(len(head))]
        return "| " + " | ".join(padded) + " |"
    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    lines = [_fmt(head), sep]
    for row in cells:
        # Pad short rows with empty strings so the pipe count stays constant.
        if len(row) < len(head):
            row = [*row, *[""] * (len(head) - len(row))]
        lines.append(_fmt(row))
    return "\n".join(lines)


def header_block(
    title: str,
    *,
    tenant: str | None = None,
    run_id: str | None = None,
    mode: str | None = None,
    extra: Mapping[str, str] | None = None,
    ts: str | None = None,
) -> str:
    """Render the H1 + single metadata line that every phase summary starts with.

    ``ts`` is included here so callers can pin it for deterministic tests.
    """
    ts = ts or utc_now_iso()
    parts: list[str] = []
    if tenant is not None:
        parts.append(f"tenant={tenant}")
    if run_id is not None:
        parts.append(f"run={run_id}")
    if mode is not None:
        parts.append(f"mode={mode}")
    if extra:
        for k in sorted(extra):
            parts.append(f"{k}={extra[k]}")
    parts.append(f"ts={ts}")
    meta = " | ".join(parts)
    return f"# {title}\n\n_{meta}_\n"


# -----------------------------------------------------------------------------
# Writing helpers
# -----------------------------------------------------------------------------

def write_json(path: Path, payload: Any) -> Path:
    """Write ``payload`` as UTF-8 JSON with sorted keys + trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def write_md(path: Path, body: str) -> Path:
    """Write ``body`` as UTF-8; ensures a single trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not body.endswith("\n"):
        body = body + "\n"
    path.write_text(body, encoding="utf-8")
    return path


def run_id_from_path(any_path_inside_run: Path) -> str:
    """Derive the ``<run>`` directory name from any path inside the run."""
    p = Path(any_path_inside_run).resolve()
    # If the path is a file, walk up to its parent; callers normally pass the
    # run directory directly.
    if p.is_file():
        p = p.parent
    return p.name
