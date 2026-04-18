---
agent: slz-readiness
name: slz-run
description: End-to-end — discover, evaluate, plan, scaffold. Pauses between phases.
---

Orchestrate the full pipeline, pausing for the user's confirmation between each phase. **Every pause MUST be implemented by calling the `ask_user` tool — never ask via plain text.** The `ask_user` call should take a single boolean field (`proceed` or `approved`) with a clear `title` so the operator sees a structured yes/no form.

**Every gate MUST also include a bounded excerpt of the upstream phase's
`<phase>.summary.md` in the form's `message` field, plus the path to the
full file.** Excerpts — not full-verbatim bodies — because scaffold
summaries in particular embed `az deployment` command blocks that render
poorly inside a form. The operator opens the path to see the full document.

1. `/slz-discover` — produces `findings.json` + `discover.summary.{md,json}`. Then call `ask_user` with a boolean `proceed` field titled **"Discover complete — continue to Evaluate?"**. The form's `message` field MUST contain: (a) a bounded excerpt of `discover.summary.md` (header, per-module status table, top observations — under ~40 lines), and (b) the path `artifacts/<run>/discover.summary.md`.
2. `/slz-evaluate` — produces `gaps.json` + `evaluate.summary.{md,json}`. Then call `ask_user` with a boolean `proceed` field titled **"Evaluate complete — continue to Plan?"**. The `message` MUST contain: (a) excerpt of `evaluate.summary.md` (header, severity tally, compliance ratio, top 5 gaps — under ~30 lines), and (b) the path `artifacts/<run>/evaluate.summary.md`.
3. `/slz-plan` — produces `plan.md` / `plan.json` / `plan.summary.{md,json}`. Then call `ask_user` with a boolean `proceed` field titled **"Plan reviewed — continue to Scaffold?"**. The `message` MUST contain: (a) excerpt of `plan.summary.md` (readiness snapshot, order-of-operations counts — under ~30 lines), and (b) the paths `artifacts/<run>/plan.summary.md` and `artifacts/<run>/plan.md`.
4. `/slz-scaffold` — produces Bicep + params + `scaffold.summary.{md,json}`. Then call `ask_user` with a boolean `approved` field titled **"Scaffold complete — run `az deployment mg what-if` yourself before acting. Acknowledged?"**. The `message` MUST contain: (a) short excerpt of `scaffold.summary.md` (emitted-template count, warnings count, gaps-not-scaffolded count — under ~20 lines), (b) the path `artifacts/<run>/scaffold.summary.md` (which holds the exact `az deployment` commands), and (c) the explicit reminder that the plugin never runs `create` — the operator does, after `what-if`.

Never collapse phases or skip pauses. Never ask via plain text — always via `ask_user`. Never run write verbs at any phase.
