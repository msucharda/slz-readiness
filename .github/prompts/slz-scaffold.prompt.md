---
agent: slz-readiness
name: slz-scaffold
description: Emit AVM-based Bicep + parameter files for the gaps.
---

Invoke the **slz-scaffold** skill. Only templates under
`scripts/scaffold/avm_templates/` are allowed. Collect parameter values from
the user, write them to `artifacts/<run>/scaffold.params.json`, then run
`slz-scaffold`. Remind the user to run `az deployment mg what-if` before any
`create`.
