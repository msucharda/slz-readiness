---
agent: slz-readiness
name: slz-run
description: End-to-end — discover, evaluate, plan, scaffold. Pauses between phases.
---

Orchestrate the full pipeline, pausing for the user's confirmation between each phase. **Every pause MUST be implemented by calling the `ask_user` tool — never ask via plain text.** The `ask_user` call should take a single boolean field (`proceed` or `approved`) with a clear `title` so the operator sees a structured yes/no form.

1. `/slz-discover` — produces `findings.json`. Then call `ask_user` with a boolean `proceed` field titled **"Discover complete — continue to Evaluate?"** before moving on.
2. `/slz-evaluate` — produces `gaps.json`. Summarise gap counts, then call `ask_user` with a boolean `proceed` field titled **"Evaluate complete — continue to Plan?"**.
3. `/slz-plan` — produces `plan.md` / `plan.json`. Then call `ask_user` with a boolean `proceed` field titled **"Plan reviewed — continue to Scaffold?"**.
4. `/slz-scaffold` — produces Bicep + params. Then call `ask_user` with a boolean `approved` field titled **"Scaffold complete — run `az deployment mg what-if` yourself before acting. Acknowledged?"**.

Never collapse phases or skip pauses. Never ask via plain text — always via `ask_user`. Never run write verbs at any phase.
