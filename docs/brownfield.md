# Brownfield tenants — running slz-readiness against an already-deployed landing zone

`slz-readiness` was strictly greenfield in v0.5.x. **v0.6.0** added the
`/slz-reconcile` phase (Evaluate retargeting only). **v0.7.0** closes
the loop: Discover, Evaluate, AND Scaffold all consume `mg_alias.json`,
giving brownfield operators end-to-end output that's actually usable.

If you've already deployed a landing zone — CAF ALZ with custom names,
in-house hierarchy, partner-delivered platform — run `/slz-reconcile`
once after `/slz-discover` and the rest of the pipeline retargets to
your real management groups.

## What v0.7.0 closes

| Phase | Pre-v0.6.0 behaviour | v0.7.0 behaviour |
|---|---|---|
| Discover | Probed only the 14 canonical SLZ MG names | Probes the **union** of canonical names + non-null entries from `mg_alias.json`; captures `policyDefinitionId`, `enforcementMode`, `notScopes` per assignment |
| Evaluate | Rules pinned `scope: mg/<slz-name>` literally | Selector-rewrite via `mg_alias.json` (v0.6.0); plus def-id fallback so renamed Microsoft built-in policy assignments still match (v0.7.0) |
| Plan | Faithfully narrated parallel-universe gaps | Narrates real gaps after retargeting + def-id equivalence |
| Scaffold | Emitted full parallel SLZ tree | Skips assignments matched by name OR `policyDefinitionId` (consequence of Evaluate's def-id fallback); `how-to-deploy.md` advertises the alias map so operators know which `MG_ID` to substitute per-template |

End-to-end on a brownfield tenant in v0.7.0:

1. `/slz-discover` enumerates customer MGs and probes both canonical
   and aliased scopes (after Reconcile has run, or just canonical on
   first pass).
2. `/slz-reconcile` walks the 14 SLZ roles and asks the operator which
   of their MGs plays each role. Writes `mg_alias.json`.
3. `/slz-discover` may be re-run after Reconcile to enrich
   `findings.json` with assignments on the now-known customer MGs.
4. `/slz-evaluate` rewrites selectors per the alias map and applies
   def-id equivalence — renamed Microsoft built-ins count as satisfied.
5. `/slz-plan` reports only the genuine gaps.
6. `/slz-scaffold` emits Bicep for assignments still missing; the
   accompanying `how-to-deploy.md` shows the alias mapping so the
   operator substitutes the right MG id at deploy time.

## Workarounds that are now obsolete

The four interim workarounds documented in v0.5.x are still listed
below for historical reference, but **with v0.7.0 only #2 remains
relevant** as a deliberate migration strategy. #1, #3, #4 are now
served by `/slz-reconcile` + the v0.7.0 retargeting tracks.

### 1. ~~Rename your MGs to SLZ shape~~ — obsolete

Use `/slz-reconcile` instead. Your MGs keep their names; the alias
file maps roles to them.

### 2. Deploy SLZ as a peer and migrate via `decommissioned`

**Still valid.** This is a real migration path, not a brownfield
patch. Choose this when your existing hierarchy is a poor fit for
sovereign workloads and you want a clean cutover. v0.7.0 doesn't
change anything about it — Scaffold's output works as-is.

### 3. ~~Hand-edit the emitted policy templates~~ — obsolete

Scaffold now reads `mg_alias.json` and the emitted `how-to-deploy.md`
explicitly tells you which MG id to use for each per-archetype
template. No string-replace required.

### 4. ~~Skip Scaffold and apply manually~~ — obsolete

Scaffold's audit-first guarantees and AVM-versioned safety are now
available on brownfield tenants too.

## Limitations still in scope for future versions

v0.7.0 closes the structural retargeting loop but does NOT yet handle:

- **Policy-parameter drift** — your assignment may have the right
  `policyDefinitionId` but customised parameters (e.g. allowed-locations
  list) that don't match the SLZ defaults. Today these still surface as
  "satisfied" by def-id; v0.8.0 will add a parameter-equivalence matcher
  (rung C in the research notes).
- **Custom initiative tree-walks** — assignments that bind to a custom
  initiative containing the canonical built-in policy will not match by
  def-id. v0.8.0 will recursively flatten initiatives (rung D).
- **Hierarchy-reshape** — if your MG tree shape (parent/child relations)
  fundamentally differs from SLZ's canonical shape, only roles you
  explicitly alias get retargeted. Genuinely incompatible shapes are
  still flagged as `mg.slz.hierarchy_shape` violations.
- **Multi-tenant platforms** — `mg_alias.json` is single-tenant.

## How `/slz-reconcile` decides

The Reconcile phase is the only LLM-writes-artifact phase in the
pipeline. Its writing path is funnelled through three guards so the
non-determinism cannot leak past it:

1. Per-role `ask_user` gate — operator confirms every proposed
   role→MG mapping individually. No batched accept.
2. Schema validator
   ([`scripts/slz_readiness/reconcile/schema.py`](../scripts/slz_readiness/reconcile/schema.py))
   — rejects bad keys, duplicate values, and (when findings.json is
   adjacent) values that don't appear in tenant's `present_ids`.
3. Post-write hook
   ([`hooks/post_tool_use.py`](../hooks/post_tool_use.py))
   — silently rewrites any non-null alias value not present in the
   sibling findings to `null`, recording the offending entry in
   `mg_alias.dropped.md`. Same loud-silent-fail pattern as the
   plan.md citation guard.

Evaluate's deterministic contract is preserved:
`(findings.json, mg_alias.json) → gaps.json` is byte-stable across
re-runs.

### Structural scoring (v0.10.0+)

The heuristic proposer in
[`scripts/slz_readiness/reconcile/proposer.py`](../scripts/slz_readiness/reconcile/proposer.py)
is a pure function over the observed MG tree — no LLM, no Azure — and
produces a best-effort `mg_alias.proposal.json` for the LLM phase to
refine. It combines substring matching with two structural signals
derived from `parent_id` + children shape:

| Signal | Weight | Applies to |
|---|---|---|
| Substring match on `id` or `displayName` | +1 | every role |
| Candidate is the tenant root (`parent_id is None`) | hard-exclude | role `slz` |
| Candidate has ≥2 children whose names look like SLZ intermediate children (`platform`, `landing*`, `workload*`, `management`, `connectivity`, `identity`, `security`, `sandbox`, `decomm*`) | +3 | role `slz` |
| Candidate's parent MG is the MG already claimed by `slz` (reinforces an existing substring hit) | +2 | roles `platform`, `landingzones`, `sandbox`, `decommissioned` |

For each role the proposer picks the unique top-scoring MG among the
unclaimed set; **ties emit `null` so the LLM per-role gate resolves
them**. Roles are processed with `slz` first (so downstream parent-
signals can reference it), then in the declared pattern order so
more-specific patterns like `confidential_corp` still claim before
the less-specific `corp`.

This replaced the v0.8.0 first-match-wins logic that mis-mapped
`slz → <tenant-root-GUID>` on real SLZ deployments where the customer
had a non-canonical intermediate MG under the tenant root.

## Reporting

If your tenant doesn't fit the patterns this doc describes (e.g.
multi-region sub-hierarchies, mixed greenfield/brownfield where some
roles exist canonically and others don't, or customer policies that
share a `policyDefinitionId` with a baseline policy but configure it
materially differently), please open an issue. v0.8.0 design depends
on understanding the long tail.

