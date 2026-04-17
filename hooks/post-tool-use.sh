#!/usr/bin/env bash
# post-tool-use.sh — citation guard for Plan-phase output.
#
# Applies only to files emitted under artifacts/<run>/plan.md. Strips any
# bullet that does not carry a `(rule_id: xxx)` citation referencing a rule
# that exists in scripts/evaluate/rules/. The stripped bullets go to
# artifacts/<run>/plan.dropped.md with the reason.
set -euo pipefail

payload=$(cat)

# Only run when the previous tool wrote a plan.md (Copilot hook convention: output_path is in payload).
plan_path=$(printf '%s' "$payload" | python3 -c '
import json, sys
d = json.load(sys.stdin)
p = d.get("output_path") or d.get("path") or ""
print(p if p.endswith("plan.md") else "")
' 2>/dev/null || true)

if [[ -z "$plan_path" || ! -f "$plan_path" ]]; then
  exit 0
fi

python3 - "$plan_path" <<'PY'
import pathlib, re, sys

plan = pathlib.Path(sys.argv[1])
rules_root = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "evaluate" / "rules"
known = {p.stem for p in rules_root.rglob("*.yml")}

kept, dropped = [], []
cite_re = re.compile(r"\(rule_id:\s*([A-Za-z0-9_.-]+)\)")
bullet_re = re.compile(r"^\s*[-*]\s+")

for line in plan.read_text(encoding="utf-8").splitlines():
    if bullet_re.match(line):
        m = cite_re.search(line)
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
    with drop_path.open("w", encoding="utf-8") as f:
        f.write("# Bullets dropped by post-tool-use citation guard\n\n")
        for line, reason in dropped:
            f.write(f"- ({reason}) {line.strip()}\n")
    print(f"post-tool-use: dropped {len(dropped)} uncited bullet(s); see {drop_path}", file=sys.stderr)
PY
