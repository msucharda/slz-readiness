---
agent: slz-readiness
name: slz-scaffold
description: Emit AVM-based Bicep + parameter files for the gaps.
---

Invoke the **slz-scaffold** skill. Only templates under
`scripts/scaffold/avm_templates/` are allowed. Collect parameter values
from the user via `ask_user` (never plain text), write them to
`artifacts/<run>/scaffold.params.json`, then run `slz-scaffold`.

The CLI writes `scaffold.summary.{md,json}` containing the emitted-template
table, warnings, "gaps NOT scaffolded" section, and the per-template
`az deployment mg what-if` / `create` commands.

When the final `ask_user` gate fires (Scaffold → acknowledge), do **not**
relay the summary as plain text and do **not** inline the full file — the
embedded deployment-command blocks will render poorly inside a form.
Instead, include in the form's `message` field:

1. A short excerpt from `scaffold.summary.md` — emitted-template count,
   warnings count, and "gaps NOT scaffolded" count. Under ~20 lines.
2. The path `artifacts/<run>/scaffold.summary.md` — emphasise that this
   file contains the exact `az deployment mg what-if` and `create`
   commands in dependency order, and the operator MUST open it and run
   `what-if` before any `create`.
3. The explicit reminder: **the plugin never runs `az deployment …
   create` — the operator does, manually, after `what-if`.**

Never ask via plain text — always via `ask_user`.
