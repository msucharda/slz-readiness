---
agent: slz-readiness
name: slz-reconcile
description: >-
  Bridge Discover and Evaluate for brownfield tenants: map the 14 canonical
  SLZ MG roles to the operator's actual MG names. Every non-null mapping
  gated by ask_user. Greenfield runs short-circuit.
---

Invoke the **slz-reconcile** skill. Like all slz phases, **never ask the
operator anything via plain text** — every question below MUST be asked
via the `ask_user` tool with the schema specified.

## 1. Establish mode via `ask_user`

Call `ask_user`:

- Field: `mode`
- Type: `string` enum
- Title: **"Greenfield or brownfield tenant?"**
- Enum values: `"greenfield"`, `"brownfield"`.
- Default: `"greenfield"`.
- Message: include an excerpt from `artifacts/<run>/discover.summary.md`
  showing the discovered MGs so the operator can answer informed.

## 2a. Greenfield → one-shot CLI

If `mode == "greenfield"`:

```bash
slz-reconcile --mode greenfield \
  --findings artifacts/<run>/findings.json \
  --out artifacts/<run>/mg_alias.json
```

Proceed to step 3.

## 2b. Brownfield → per-role proposal loop

If `mode == "brownfield"`:

### Optional fast path: heuristic pre-fill

Before the per-role loop, call `ask_user`:

- Field: `use_heuristic`
- Type: `boolean`
- Title: **"Try a heuristic proposal first?"**
- Default: `true`
- Message: "The heuristic substring-matches your MG names (`corp-mg`,
  `Management`, `Sandbox`, etc.) against canonical SLZ roles. Unsure
  roles come back as `null` and you confirm them manually. Skip the
  heuristic if your MGs use opaque names."

If `use_heuristic == true`, run:

```bash
slz-reconcile --mode brownfield --heuristic \
  --findings artifacts/<run>/findings.json \
  --out artifacts/<run>/mg_alias.json
```

If the CLI accepts, render the resulting alias map to the operator
via `ask_user` with a boolean `accept` field titled **"Accept the
heuristic mapping?"**. On rejection, fall through to the per-role
loop below. On acceptance, proceed to step 3.

### Per-role proposal loop

For each of the 14 canonical SLZ roles (iterate in stable order —
alphabetic by role name — for deterministic operator experience):

1. Inspect `findings.json` and candidate customer MGs from
   `present_ids`. Use `sequential-thinking` to rank candidates on
   child-sub count, inherited policy assignments, and
   name-similarity (in that order of weight).
2. If a strong candidate exists, build an impact card with the
   `Proposal` dataclass + `impact.render()` helper from
   `scripts/slz_readiness/reconcile/impact.py`. Cap at 5 evidence
   bullets and 4 impact bullets. Do not embed arbitrary markdown;
   the helper emits the fixed layout.
3. Call `ask_user`:
   - Field: `decision`
   - Type: `string` enum
   - Title: **"Role `<role>` → map to `<customer_mg>`?"**
   - Enum values: `"accept"`, `"reject"`, `"skip"`.
   - Message: the rendered impact card.
4. Accumulate accepted mappings into a dict keyed by role.
5. Never propose the same customer MG for two roles — track used MGs
   in the loop state.

After the loop, write the accumulated dict to
`artifacts/<run>/mg_alias.proposal.json` (include every role — map to
`null` for skipped / rejected roles), then run:

```bash
slz-reconcile --mode brownfield \
  --findings artifacts/<run>/findings.json \
  --proposal artifacts/<run>/mg_alias.proposal.json \
  --out artifacts/<run>/mg_alias.json
```

If the CLI exits non-zero (schema violation):

- Surface the CLI's error message to the operator via one more
  `ask_user` with a boolean `retry` field titled **"Schema rejected
  the alias map. Retry with corrections?"**
- On `retry == true`, return to the proposal loop.

## 3. Relay the summary

The CLI writes `artifacts/<run>/reconcile.summary.md`. The follow-up
`ask_user` gate (between Reconcile and Evaluate, driven by `/slz-run`)
must include:

1. An excerpt of `reconcile.summary.md` (header, mode, roles-mapped
   count, the role mapping table — under ~25 lines).
2. The path `artifacts/<run>/reconcile.summary.md`.
3. If `mode == "brownfield"` and any roles were mapped, a short
   paragraph reminding the operator that **Scaffold** emits Bicep
   with canonical role names (so templates stay reusable across
   tenants) and surfaces the alias mapping as a substitution table
   in the generated `how-to-deploy.md`. The operator (or their
   pipeline) substitutes `MG_ID` per template using that table at
   deploy time. In-place Bicep MG-name rewriting is on the v0.8.0
   roadmap.

Do not interpret the mappings yourself; Evaluate reads the file and
rewrites selectors deterministically.
