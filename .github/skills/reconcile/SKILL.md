---
name: slz-reconcile
description: |
  Bridge between Discover and Evaluate for brownfield tenants. Proposes a
  mapping from canonical SLZ management-group roles to the operator's
  actual MG names, gated behind per-role ask_user confirmations.
  Greenfield runs short-circuit to an all-null alias. Writes
  ``artifacts/<run>/mg_alias.json`` (schema-validated) and
  ``reconcile.summary.{md,json}``.
tools: [shell, sequential-thinking]
---

# slz-reconcile skill (v0.6.0 skeleton)

This phase is the **only** LLM-writes-artifact phase in the pipeline.
Its job is narrow: propose role→MG mappings, let the operator accept or
reject each one via `ask_user`, then hand the accepted set to the
`slz-reconcile` CLI which is a deterministic schema-gated writer.

Evaluate's zero-LLM contract is preserved: it consumes the validated
`mg_alias.json` as data, not reasoning.

## Procedure

### 1. Load Discover findings

```bash
cat artifacts/<run>/findings.json
```

Extract `present_ids` from the `microsoft.management/managementgroups.summary`
finding. This is the tenant's actual MG list.

### 2. Ask `ask_user`: is this a greenfield or brownfield tenant?

Field: `mode` (string enum).
Values: `"greenfield"`, `"brownfield"`.
Title: **"Is this tenant being taken to SLZ from scratch, or does it
already operate a landing zone?"**
Message: include a short excerpt from `discover.summary.md` showing the
MGs that were discovered, so the operator can see what the tenant
actually looks like before choosing.

### 3a. Greenfield path — short-circuit

Run:

```bash
slz-reconcile --mode greenfield \
  --findings artifacts/<run>/findings.json \
  --out artifacts/<run>/mg_alias.json
```

Writes `{"<role>": null, …}` for all 14 SLZ roles and exits 0. Skip
directly to step 5.

### 3b. Brownfield path — per-role proposal loop

For each of the 14 canonical SLZ roles (see `CANONICAL_ROLES` in
`scripts/slz_readiness/reconcile/__init__.py`):

1. Use `sequential-thinking` to evaluate whether a customer MG from
   `present_ids` plays the equivalent SLZ role. Signals:
   - child-subscription count and workload characterisation
   - inherited policy assignments (from `findings.json`)
   - name similarity (weakest signal — do not rely on alone)
2. If a strong candidate exists, build a `Proposal` (see
   `scripts/slz_readiness/reconcile/impact.py`) with at most 5
   evidence lines and at most 4 impact lines (the rules that will
   re-evaluate — use the `rules_affected_by(role)` helper once
   implemented; for v0.6.0, list the archetype rules manually).
3. Render the Proposal via `impact.render()` into an `ask_user` form's
   `message` field. The form itself has:
   - Field: `decision` (string enum: `"accept"`, `"reject"`, `"skip"`)
   - Title: **"Role `<role>` → `<customer_mg>` — accept this mapping?"**
4. If `decision == "accept"`, record the mapping.
5. If `decision == "reject"`, re-propose a different candidate OR
   leave the role null (operator discretion).
6. If `decision == "skip"`, leave the role null.

Never propose the same customer MG for two roles — the schema
rejects duplicates and the CLI will exit non-zero.

### 4. Hand the accepted set to the CLI

Write the accumulated mapping to
`artifacts/<run>/mg_alias.proposal.json` (plain JSON, same shape as
the final alias map), then:

```bash
slz-reconcile --mode brownfield \
  --findings artifacts/<run>/findings.json \
  --proposal artifacts/<run>/mg_alias.proposal.json \
  --out artifacts/<run>/mg_alias.json
```

If the CLI exits non-zero (schema violation), surface the error to
the operator via a final `ask_user` boolean asking whether to retry
with corrected mappings.

### 5. Relay the summary to the next gate

The CLI writes `artifacts/<run>/reconcile.summary.md`. When `/slz-run`
pauses between Reconcile and Evaluate, include an excerpt of this
file (mode, roles-mapped count, role mapping table) in the next gate's
`ask_user` message, plus the path.

## Boundaries

- **Do not** modify `mg_alias.json` after the CLI writes it. The
  post-tool-use hook (v0.6.0 defers this guard; planned for v0.6.1)
  is the only writer allowed after the CLI.
- **Do not** infer mappings from web searches or documentation.
  Evidence must come from `findings.json` or the operator.
- **Do not** auto-accept mappings. Every non-null entry MUST pass
  through an explicit `ask_user` accept.
