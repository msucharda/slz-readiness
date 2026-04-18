---
agent: slz-readiness
name: slz-evaluate
description: Run the deterministic rule engine against findings.json.
---

Invoke the **slz-evaluate** skill. Use the most recent
`artifacts/<run>/findings.json` unless the user specifies otherwise. Emit
`gaps.json`. **No LLM reasoning in this step** — the engine is pure Python.

After the CLI exits it writes `artifacts/<run>/evaluate.summary.{md,json}`
(totals, severity tally, compliance ratio, top-N gaps).

When the next `ask_user` gate fires (Evaluate → Plan), do **not** relay the
summary as plain text. Instead, include in the form's `message` field:

1. A short excerpt from `evaluate.summary.md` — header, severity tally,
   compliance ratio, and top 5 gap rows. Under ~30 lines.
2. The path `artifacts/<run>/evaluate.summary.md` for the full file.

Never ask via plain text — always via `ask_user`.
