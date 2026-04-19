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
1. **Pre-flight the parameter sidecar (v0.12.1).** Run the CLI first with
   NO `--params` to produce `artifacts/<run>/scaffold.params.auto.json`.
   Inspect its `needs_operator_input` list:
   - If **non-empty**, you MUST call `ask_user` BEFORE any
    `accept_defaults` form, with two distinct fields:
     - `primary_location` (enum, single) — drives
       `archetype-policies.identityLocation`.
     - `allowed_locations` (array, multi-select) — drives BOTH
       `sovereignty-global-policies.listOfAllowedLocations` and
       `sovereignty-confidential-policies.listOfAllowedLocations`.
       Must be a superset of `primary_location`; re-ask if not.
     The `message` must warn the operator that empty
     `listOfAllowedLocations` denies every region under enforce and
     flags every resource under audit.
   - If **empty**, the modal region derived from findings is sufficient
     — skip to step 2 with the `accept_defaults` form.

   Use sequential-thinking to reason about other parameter
   dependencies (display names, retention, etc.) only after the
   location gate is resolved.
2. Write a params JSON file keyed by template stem:
   ```json
   {
     "management-groups": { "parentManagementGroupId": "<tenant-id>" },
     "archetype-policies": { "identityLocation": "<primary_location>" },
     "sovereignty-global-policies": { "listOfAllowedLocations": ["<region>", ...] },
     "sovereignty-confidential-policies": { "listOfAllowedLocations": ["<region>", ...] }
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
4. The CLI writes `scaffold.summary.{json,md}` next to the manifest with:
   emitted-template table (with rule_ids closed), warnings, a "Gaps NOT
   scaffolded" section, and per-template `az deployment mg what-if`/`create`
   commands in dependency order.

   When the final `ask_user` gate fires, include in the form's `message`
   field a bounded excerpt (emitted-template count, warnings count,
   "gaps NOT scaffolded" count — under ~20 lines) plus the path
   `artifacts/<run>/scaffold.summary.md`. Do NOT inline the full file
   — the embedded `az deployment` command blocks render poorly inside a
   form. Emphasise that the operator must open the summary file and run
   `az deployment mg what-if` themselves before any `create`; the plugin
   never runs deploy verbs.
5. If all four phase summaries are present in the run dir, the CLI also
   writes `run.summary.md` — a concatenated roll-up suitable for sharing
   with stakeholders who didn't watch the live run.
