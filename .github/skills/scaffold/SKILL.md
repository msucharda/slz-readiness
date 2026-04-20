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

> **Canonical invocation.** All `slz_readiness` CLIs are invoked as
> `python -m slz_readiness.<phase>.cli …`. The `slz-<phase>`
> console-script shim installed by `pip install -e .` is a convenience
> alias for interactive use — prefer the `python -m` form in
> scripted/agent contexts (it does not depend on the venv's `Scripts/`
> or `bin/` directory being on `PATH`).

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
1. **Ask the scaffold-profile gate (v0.13.0).** Before any CLI run, call
   `ask_user` with a single `enum` field `scaffold_profile` whose
   options are:
   - `full` (default) — emit every applicable template and auto-deploy
     the ~191 ALZ custom policy + policySet definitions at the SLZ
     intermediate-root MG. This is the only profile that produces a
     deployment which passes `what-if` end-to-end. Choose this when the
     operator wants the full SLZ archetype overlay.
   - `minimal` — emit only `management-groups` and the sovereignty-*
     templates. Skip archetype policies AND the custom-def infra. Use
     for operators who need sovereignty-only compliance and are not
     ready for the full ALZ policy catalogue.
   - `include-placeholders` — `full` plus emit the ~5 assignments
     whose baseline parameters still carry ALZ placeholders (DDoS plan
     IDs, Private DNS zone resource IDs). The operator commits to
     hand-editing the emitted `*.parameters.json` BEFORE running
     `az deployment … create`; `what-if` will fail otherwise.

   The `message` must warn the operator that `full` is the right
   default for production SLZ rollout — `minimal` trades governance
   coverage for simplicity; `include-placeholders` requires manual
   editing before deploy.

   In the SAME `ask_user` form, include a second boolean field
   `emit_deploy_script` (default `false`). When `true`, the CLI is
   invoked with `--emit-deploy-script` and emits an opt-in one-shot
   orchestrator at `artifacts/<run>/runbooks/deploy-all.{ps1,sh}`
   (plus `grant-dine-roles.{ps1,sh}` when archetype-policies is in
   the emit set). The script defaults to `-WhatIf` / `--whatif`;
   passing `-Apply` / `--apply` runs `create`. Wave 1 (audit) only.
   **The agent cannot execute the emitted script** —
   `hooks/pre_tool_use.py` blocks any invocation of
   `deploy-all.{ps1,sh}` or `grant-dine-roles.{ps1,sh}`. The
   operator runs it themselves.

2. **Pre-flight the parameter sidecar (v0.12.1).** Run the CLI first with
   NO `--params` (but DO pass `--scaffold-profile <choice>` and
   `--emit-deploy-script` if the operator opted in) to produce
   `artifacts/<run>/scaffold.params.auto.json`.
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
     — skip to step 3 with the `accept_defaults` form.

   Use sequential-thinking to reason about other parameter
   dependencies (display names, retention, etc.) only after the
   location gate is resolved.
3. Write a params JSON file keyed by template stem:
   ```json
   {
     "management-groups": { "parentManagementGroupId": "<tenant-id>" },
     "archetype-policies": { "identityLocation": "<primary_location>" },
     "sovereignty-global-policies": { "listOfAllowedLocations": ["<region>", ...] },
     "sovereignty-confidential-policies": { "listOfAllowedLocations": ["<region>", ...] }
   }
   ```
4. Run (canonical form — works without the venv shim on PATH):
   ```bash
   python -m slz_readiness.scaffold.cli --gaps artifacts/<run>/gaps.json \
                --params artifacts/<run>/scaffold.params.json \
                --scaffold-profile <full|minimal|include-placeholders> \
                --out    artifacts/<run>
   # interactive shim equivalent:
   slz-scaffold --gaps artifacts/<run>/gaps.json \
                --params artifacts/<run>/scaffold.params.json \
                --scaffold-profile <full|minimal|include-placeholders> \
                --out    artifacts/<run>
   ```
5. The CLI writes `scaffold.summary.{json,md}` next to the manifest with:
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
6. If all four phase summaries are present in the run dir, the CLI also
   writes `run.summary.md` — a concatenated roll-up suitable for sharing
   with stakeholders who didn't watch the live run.
