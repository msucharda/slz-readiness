---
agent: slz-readiness
name: slz-run
description: End-to-end — discover, evaluate, plan, scaffold. Pauses between phases.
---

Orchestrate the full pipeline, pausing for the user's confirmation between each phase:

1. `/slz-discover` — produces `findings.json`. **PAUSE** and ask the user to confirm before evaluating.
2. `/slz-evaluate` — produces `gaps.json`. **PAUSE** and summarise gap counts before planning.
3. `/slz-plan` — produces `plan.md` / `plan.json`. **PAUSE** and ask the user to review.
4. `/slz-scaffold` — produces Bicep + params. **PAUSE** and remind the user to run `az deployment mg what-if`.

Never collapse phases or skip pauses. Never run write verbs at any phase.
