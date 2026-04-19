---
agent: slz-readiness
name: slz-scaffold
description: Emit AVM-based Bicep + parameter files for the gaps.
---

Invoke the **slz-scaffold** skill. Only templates under
`scripts/scaffold/avm_templates/` are allowed.

## Parameter pre-fill (v0.9.0)

`slz-scaffold` derives parameter defaults from
`artifacts/<run>/findings.json` automatically — operators no longer
have to enumerate every parameter by hand. The tool derives:

* `management-groups.parentManagementGroupId` ← `run_scope.tenant_id`
* `log-analytics.workspaceName` / `location` / `resourceGroupName` ←
  the first workspace observed (deterministic sort by
  `(subscriptionId, id)`).
* `archetype-policies.identityLocation` and
  `sovereignty-global-policies.listOfAllowedLocations` ← the modal
  region across observed workspaces (alphabetical tiebreak).

**UX flow:**

1. Run `python -m slz_readiness.scaffold.cli --gaps ... --out ...` once
   with NO `--params` flag. The CLI writes
   `artifacts/<run>/scaffold.params.auto.json` containing the merged
   param set + per-key `derived` / `operator_override` origin + a
   `needs_operator_input` list of policy-critical keys prefill could
   not derive from findings (v0.12.1 — see **Location gate** below).
2. **Location gate (v0.12.1 — MANDATORY).** Inspect the sidecar's
   `needs_operator_input` list. If it is non-empty **you MUST** call
   `ask_user` BEFORE presenting the `accept_defaults` form, with a
   form that has two distinct fields:

   * `primary_location` — `enum`, single select, drives
     `archetype-policies.identityLocation`. List the commonly-used
     Azure regions for the tenant's cloud (e.g. `westeurope`,
     `northeurope`, `swedencentral`, `germanywestcentral`,
     `eastus2`, `westus3`, …) and include a free-text fallback only
     if the discovered region isn't in the list.
   * `allowed_locations` — `array` of strings (multi-select), drives
     both `sovereignty-global-policies.listOfAllowedLocations` and
     `sovereignty-confidential-policies.listOfAllowedLocations`.
     Must contain `primary_location`; reject and re-ask if it does
     not (silently permitting deny-all is the failure mode this gate
     is guarding).

   The `message` field MUST spell out the risk: an empty
   `listOfAllowedLocations` denies every region under
   `rolloutPhase=enforce` and flags every resource as non-compliant
   under `audit`. Write the operator's choice into
   `artifacts/<run>/scaffold.params.json` and re-run the CLI with
   `--params`.
3. Show the derived values to the operator via a single `ask_user`
   form with a boolean field `accept_defaults`. Include the
   `params_by_template` block verbatim in the form `message`. This
   step runs AFTER the location gate (step 2) so the form reflects
   the operator's chosen regions.
4. If the operator declines, iterate per stem with one `ask_user` per
   template stem whose values they want to tweak. Write the overrides
   to `artifacts/<run>/scaffold.params.json`, then invoke
   `slz-scaffold` again with `--params` pointing at that file.
5. Operator-supplied keys for `archetype-policies.assignments` are
   stripped with a warning — that field is engine-owned (rebuilt from
   the baseline every run).

Never ask via plain text — always via `ask_user`. Never collapse the
two location fields into a single question; primary vs. allowed have
different semantics and must be captured independently.

## Brownfield rewrite gate (v0.8.0)

Before invoking the CLI, if `artifacts/<run>/mg_alias.json` exists and
has any non-null values, call `ask_user` with a boolean field asking:

> _"Rewrite canonical SLZ MG names to your tenant's actual MG names
> inside the emitted Bicep? YES → apply-ready Bicep for this tenant
> (no manual substitution needed before `az deployment`). NO → emit
> canonical names and a substitution table in `how-to-deploy.md`
> (useful for cross-tenant reuse)."_

Default YES when `mg_alias.json` has non-null entries. Pass
`--rewrite-names` to `slz-scaffold` when the user accepts. When no
alias map exists, do not ask — the flag is a no-op.

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
