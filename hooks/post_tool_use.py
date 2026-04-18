#!/usr/bin/env python3
"""post_tool_use hook — citation guard for Plan-phase output AND
schema guard for Reconcile-phase output.

Cross-platform replacement for hooks/post-tool-use.sh.

Two file types are policed:

* ``artifacts/<run>/plan.md`` — strip every bullet that doesn't carry a
  ``(rule_id: xxx)`` citation referencing a rule under
  ``scripts/evaluate/rules/``. Stripped bullets land in
  ``artifacts/<run>/plan.dropped.md`` with the reason.
* ``artifacts/<run>/mg_alias.json`` — repair structural issues the LLM
  is likely to introduce, then rewrite any non-null alias value that
  does NOT appear in the sibling ``findings.json``'s ``present_ids``
  to ``null``. Rejected entries land in
  ``artifacts/<run>/mg_alias.dropped.md``. Structural repairs:
  drop unknown keys (not in ``CANONICAL_ROLES``); null duplicate
  values (two roles mapping to the same MG); null non-string,
  non-null values. This is defense-in-depth in case the LLM writes
  the file directly via Copilot's file-write tool, bypassing the
  Reconcile CLI's schema validator.

Contract is identical to the bash version: stdin = JSON payload with an
``output_path`` or ``path`` field; exit 0 always (a parsing failure should
never block the tool call).
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import Counter

BULLET_RE = re.compile(r"^\s*[-*]\s+")
CITE_RE = re.compile(r"\(rule_id:\s*([A-Za-z0-9_.-]+)\)")

# The 14 canonical SLZ roles. Duplicated from
# scripts/slz_readiness/reconcile/__init__.py:CANONICAL_ROLES — the hook
# is intentionally importable without the slz_readiness package on PYTHONPATH
# (it runs from the plugin directory in some hosts), so we mirror the list
# rather than import. Keep in lockstep with reconcile.CANONICAL_ROLES.
CANONICAL_ROLES: frozenset[str] = frozenset({
    "confidential_corp",
    "confidential_online",
    "connectivity",
    "corp",
    "decommissioned",
    "identity",
    "landingzones",
    "management",
    "online",
    "platform",
    "public",
    "sandbox",
    "security",
    "slz",
})


def _rules_root() -> pathlib.Path:
    # hooks/post_tool_use.py  →  repo-root / scripts / evaluate / rules
    return pathlib.Path(__file__).resolve().parents[1] / "scripts" / "evaluate" / "rules"


def _known_rule_ids() -> set[str]:
    root = _rules_root()
    if not root.exists():
        return set()
    return {p.stem for p in root.rglob("*.yml")}


def _extract_plan_path(payload: dict) -> pathlib.Path | None:
    p = payload.get("output_path") or payload.get("path") or ""
    if not isinstance(p, str) or not p.endswith("plan.md"):
        return None
    path = pathlib.Path(p)
    return path if path.is_file() else None


def _extract_alias_path(payload: dict) -> pathlib.Path | None:
    p = payload.get("output_path") or payload.get("path") or ""
    if not isinstance(p, str):
        return None
    path = pathlib.Path(p)
    if path.name != "mg_alias.json" or not path.is_file():
        return None
    return path


def _filter_plan(plan: pathlib.Path, known: set[str]) -> int:
    kept: list[str] = []
    dropped: list[tuple[str, str]] = []
    for line in plan.read_text(encoding="utf-8").splitlines():
        if BULLET_RE.match(line):
            m = CITE_RE.search(line)
            if not m:
                dropped.append((line, "no rule_id cited"))
                continue
            if m.group(1) not in known:
                dropped.append((line, f"unknown rule_id '{m.group(1)}'"))
                continue
        kept.append(line)
    plan.write_text("\n".join(kept) + "\n", encoding="utf-8")
    if dropped:
        drop_path = plan.with_suffix(".dropped.md")
        with drop_path.open("w", encoding="utf-8") as fh:
            fh.write("# Bullets dropped by post-tool-use citation guard\n\n")
            for line, reason in dropped:
                fh.write(f"- ({reason}) {line.strip()}\n")
        print(
            f"post-tool-use: dropped {len(dropped)} uncited bullet(s); see {drop_path}",
            file=sys.stderr,
        )
    return len(dropped)


def _load_findings_present_ids(run_dir: pathlib.Path) -> set[str] | None:
    """Extract ``present_ids`` from ``run_dir/findings.json``.

    Returns ``None`` when findings is missing/unreadable/lacks the
    MG summary record — caller treats that as "skip the guard"
    rather than dropping every alias.
    """
    findings_path = run_dir / "findings.json"
    if not findings_path.is_file():
        return None
    try:
        doc = json.loads(findings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    records = doc.get("findings") if isinstance(doc, dict) else doc
    if not isinstance(records, list):
        return None
    for f in records:
        if not isinstance(f, dict):
            continue
        if f.get("resource_type") != "microsoft.management/managementgroups.summary":
            continue
        obs = f.get("observed_state")
        if isinstance(obs, dict):
            ids = obs.get("present_ids")
            if isinstance(ids, list):
                return {str(x) for x in ids}
    return None


def _filter_alias(alias_path: pathlib.Path) -> int:
    """Repair the four failure modes the Reconcile LLM is likely to introduce.

    Returns the total number of repairs applied. Always exits cleanly so a
    bad file never blocks tool execution. Repairs (in order):

    1. Drop unknown top-level keys (not in :data:`CANONICAL_ROLES`).
    2. Null any value that is neither ``str`` nor ``None`` (e.g. numbers,
       booleans, nested dicts).
    3. Null duplicate non-null string values (two roles mapping to the same
       customer MG would double-count assignments).
    4. Null any string value not present in sibling ``findings.json``
       ``present_ids`` (LLM hallucinated a customer MG that doesn't exist).

    All four modes are surfaced in ``mg_alias.dropped.md`` with the reason.
    """
    try:
        raw = json.loads(alias_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(raw, dict):
        return 0

    repairs: list[tuple[str, str, str]] = []  # (role, original_value, reason)
    out: dict[str, object] = {}

    # Pass 1 — drop unknown keys; null bad-type values; canonicalise
    # whitespace on string values so duplicate detection in pass 2 is
    # robust to "corp " vs "corp".
    for role, value in raw.items():
        if not isinstance(role, str) or role not in CANONICAL_ROLES:
            repairs.append((str(role), repr(value), "unknown role key"))
            continue
        if value is None:
            out[role] = None
            continue
        if not isinstance(value, str):
            repairs.append((role, repr(value), f"non-string value (type {type(value).__name__})"))
            out[role] = None
            continue
        stripped = value.strip()
        if not stripped:
            repairs.append((role, value, "empty/whitespace-only value"))
            out[role] = None
            continue
        out[role] = stripped

    # Pass 2 — null duplicates.
    counts = Counter(v for v in out.values() if isinstance(v, str))
    dupes = {v for v, c in counts.items() if c > 1}
    if dupes:
        for role, value in list(out.items()):
            if isinstance(value, str) and value in dupes:
                repairs.append((role, value, "duplicate value (also assigned to other role(s))"))
                out[role] = None

    # Pass 3 — null values not in findings.present_ids.
    present = _load_findings_present_ids(alias_path.parent)
    if present is not None:
        for role, value in list(out.items()):
            if isinstance(value, str) and value not in present:
                repairs.append((role, value, "not in tenant findings present_ids"))
                out[role] = None

    if not repairs:
        return 0

    alias_path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    drop_path = alias_path.with_suffix(".dropped.md")
    with drop_path.open("w", encoding="utf-8") as fh:
        fh.write("# Aliases repaired by post-tool-use schema guard\n\n")
        fh.write(
            "These role→MG entries were repaired (dropped or nulled) because "
            "they failed structural or tenant-membership checks. The hook "
            "applies these repairs as defense-in-depth — the Reconcile CLI "
            "validator covers the same checks at write-time, but the hook "
            "fires on every file write (including direct LLM edits via "
            "Copilot's file-write tool that bypass the CLI). Re-run "
            "`slz-reconcile` to regenerate a clean mapping.\n\n"
        )
        for role, value, reason in repairs:
            fh.write(f"- `{role}` → `{value}` — {reason}\n")
    print(
        f"post-tool-use: repaired {len(repairs)} alias entry/entries; see {drop_path}",
        file=sys.stderr,
    )
    return len(repairs)


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    plan = _extract_plan_path(payload)
    if plan is not None:
        _filter_plan(plan, _known_rule_ids())
        return 0
    alias = _extract_alias_path(payload)
    if alias is not None:
        _filter_alias(alias)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
