---
agent: slz-readiness
name: slz-plan
description: Produce a prioritised, cited remediation plan from gaps.json.
---

Invoke the **slz-plan** skill. Every bullet MUST cite a `rule_id` from
`gaps.json`; uncited bullets are removed by the post-tool-use hook. Group by
design area, order by dependency (MG hierarchy first, policies after).

The skill produces `plan.md`, `plan.json`, and `plan.summary.{md,json}`
(readiness snapshot, order-of-operations counts, discovery blind spots).

When the next `ask_user` gate fires (Plan → Scaffold), do **not** relay the
summary as plain text. Include in the form's `message` field:

1. A short excerpt from `plan.summary.md` — readiness snapshot and
   order-of-operations counts. Under ~30 lines.
2. The path `artifacts/<run>/plan.summary.md` for the full file.
3. A reminder that `plan.md` is the human-readable plan itself (distinct
   from the summary).

Never ask via plain text — always via `ask_user`.
