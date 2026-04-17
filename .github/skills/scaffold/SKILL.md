---
name: slz-scaffold
description: >-
  Emit Bicep + parameter files for each gap by filling a pinned AVM template.
  Never write free-form Bicep — only the templates under
  scripts/scaffold/avm_templates/ are allowed.
tools:
  - shell
  - sequential-thinking
---

# slz-scaffold skill

## Goal
Produce `artifacts/<run>/bicep/*.bicep`, `artifacts/<run>/params/*.parameters.json`,
and `artifacts/<run>/scaffold.manifest.json`.

## Hard rules
- **No free-form Bicep.** You may only copy templates from
  `scripts/scaffold/avm_templates/` via the scaffold engine.
- **Parameters must validate** against the matching JSON schema in
  `scripts/scaffold/param_schemas/`. The engine refuses invalid params.
- **Never deploy.** The plugin only emits files; the human operator runs
  `az deployment … what-if` and `create`.

## Procedure
1. Decide parameter values with the user (display names, location, retention,
   etc.). Use sequential-thinking to reason about dependencies.
2. Write a params JSON file keyed by template stem:
   ```json
   {
     "management-groups": { "parentManagementGroupId": "<tenant-id>" },
     "sovereignty-policies": { "deployConfidential": true }
   }
   ```
3. Run (console script, or portable `python -m` form):
   ```bash
   slz-scaffold --gaps artifacts/<run>/gaps.json \
                --params artifacts/<run>/scaffold.params.json \
                --out    artifacts/<run>
   # portable equivalent:
   python -m slz_readiness.scaffold.cli --gaps artifacts/<run>/gaps.json \
                --params artifacts/<run>/scaffold.params.json \
                --out    artifacts/<run>
   ```
4. Show the `scaffold.manifest.json` to the user and remind them to run
   `az deployment mg what-if …` before any `create`.
