#!/usr/bin/env python3
"""post_tool_use hook — citation guard for Plan-phase output.

Cross-platform replacement for hooks/post-tool-use.sh. When the previous tool
wrote an ``artifacts/<run>/plan.md``, strip every bullet that doesn't carry a
``(rule_id: xxx)`` citation referencing a rule under
``scripts/evaluate/rules/``. Stripped bullets land in
``artifacts/<run>/plan.dropped.md`` with the reason.

Contract is identical to the bash version: stdin = JSON payload with an
``output_path`` or ``path`` field; exit 0 always (a parsing failure should
never block the tool call).
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

BULLET_RE = re.compile(r"^\s*[-*]\s+")
CITE_RE = re.compile(r"\(rule_id:\s*([A-Za-z0-9_.-]+)\)")


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


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    plan = _extract_plan_path(payload)
    if plan is None:
        return 0
    _filter_plan(plan, _known_rule_ids())
    return 0


if __name__ == "__main__":
    sys.exit(main())
