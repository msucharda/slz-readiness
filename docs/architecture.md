# Architecture

`slz-readiness` is a Copilot plugin that audits an Azure tenant for
**Sovereign Landing Zone (SLZ)** readiness against a **vendored, SHA-pinned**
Cloud Adoption Framework baseline.

## Five phases (four deterministic + one LLM-writes-artifact)

```
┌──────────┐ findings.json ┌───────────┐ mg_alias.json ┌──────────┐ gaps.json ┌──────┐ plan.md ┌──────────┐
│ Discover │──────────────▶│ Reconcile │──────────────▶│ Evaluate │──────────▶│ Plan │────────▶│ Scaffold │
│ (az,MCP) │               │ (LLM +    │               │ (pure py)│           │(LLM+ │         │ (AVM     │
│ readonly │               │  ask_user)│               │  NO LLM  │           │SeqTh)│         │  Bicep)  │
└──────────┘               └───────────┘               └──────────┘           └──────┘         └──────────┘
       └──────────── re-run after Reconcile to enrich aliased-MG probes ────────┘
```

- **Discover** (`skills/discover`, `scripts/slz_readiness/discover/`) — runs
  `az ... list|show|query` and emits `findings.json`. Probes the union of
  canonical SLZ MG names AND any non-null entries from `mg_alias.json` (when
  re-run after Reconcile). Captures `policyDefinitionId`, `enforcementMode`,
  `notScopes` per assignment so Evaluate can match by definition-id.
- **Reconcile** (`skills/reconcile`, `scripts/slz_readiness/reconcile/`) —
  the only LLM-writes-artifact phase. Walks the 14 SLZ roles and asks the
  operator (per-role `ask_user` enum) which of their MGs plays each role.
  Writes `mg_alias.json`. Schema-validated by `reconcile/schema.py`;
  non-null values that don't appear in tenant's `present_ids` are silently
  rewritten to `null` by the post-tool-use hook and recorded in
  `mg_alias.dropped.md`. Greenfield short-circuits to an all-null map.
- **Evaluate** (`skills/evaluate`, `scripts/slz_readiness/evaluate/`) — pure
  Python. Each rule YAML under `scripts/evaluate/rules/` declares a
  `baseline.path@sha`. If the file's SHA drifts, the rule refuses to load.
  Reads `mg_alias.json` and rewrites `matcher.selector.scope: mg/<role>` to
  the aliased name. The `archetype_policies_applied` matcher additionally
  matches by `policyDefinitionId` when assignment names differ — renamed
  Microsoft built-ins count as satisfied.
- **Plan** (`skills/plan`) — LLM + `sequential-thinking` narrates the gaps.
  Every bullet must cite a `rule_id`; the post-tool-use hook strips any bullet
  that doesn't.
- **Scaffold** (`skills/scaffold`, `scripts/slz_readiness/scaffold/`) — fills
  the pinned AVM Bicep templates under `scripts/scaffold/avm_templates/`.
  Free-form Bicep is not possible — parameters validate against JSON schemas.
  Reads `mg_alias.json` and surfaces aliased role→MG mappings in the emitted
  `how-to-deploy.md`. Skips assignments already matched by name OR
  `policyDefinitionId` (the matcher's def-id fallback excludes them from
  `gap.observed.missing`, so Scaffold never sees them).
  Policy templates ship in **Audit-first** mode: `rolloutPhase=audit` (default)
  rewrites every baseline `Deny` effect to `Audit` at emit-time, producing a
  Wave 1 deployment that records compliance without blocking writes. Operators
  re-scaffold with `rolloutPhase=enforce` for Wave 2 after observing Azure
  Policy state. Each run emits `how-to-deploy.md` with paired `what-if` +
  `create` recipes in both PowerShell and Bash. Baseline JSON under
  `data/baseline/alz-library/` is never mutated — the rewrite is a pure
  transform in `engine._downshift_deny_to_audit`.

### LLM-writes-artifact invariant

Reconcile is the only phase whose **output** is shaped by an LLM. The
non-determinism is funnelled through three guards:

1. **Per-decision `ask_user`** — operator confirms every role→MG mapping
   individually with a structured enum. No batched accept.
2. **Schema validator** — `reconcile/schema.py` rejects bad keys, duplicate
   values, and (when sibling `findings.json` is present) values not in
   `present_ids`.
3. **Post-write hook** — `hooks/post_tool_use.py` re-validates
   `mg_alias.json` on every write, replacing offending non-null entries
   with `null` and appending them to `mg_alias.dropped.md`
   (silent-fail-loud). Same pattern as the existing `plan.md` citation
   guard.

Evaluate's deterministic contract is preserved:
`(findings.json, mg_alias.json) → gaps.json` is byte-stable across re-runs.

## Trace events

`artifacts/<run>/trace.jsonl` records every meaningful decision. Brownfield
(v0.7.0) added these events:

| Event | Phase | Emitted when |
|---|---|---|
| `discover.alias.loaded` / `discover.alias.skip` | Discover | At startup; loaded only if `mg_alias.json` exists in run dir |
| `discover.extra_mg_probed` | Discover | Each customer-MG scope added to the SCOPES union |
| `evaluate.alias.loaded` / `evaluate.alias.skip` | Evaluate | At engine startup |
| `evaluate.definition_id_match` | Evaluate | Per assignment matched by `policyDefinitionId` (not by name) |
| `scaffold.alias.loaded` / `scaffold.alias.skip` | Scaffold | At engine startup |
| `scaffold.skip_existing` | Scaffold | Per archetype-policies bucket where some required assignments were already present |

## Data contracts

- `findings.json` — `{ findings: [{ resource_type, resource_id, scope, observed_state, query_cmd }] }`. Policy-assignment `observed_state` includes `policyDefinitionId`, `enforcementMode`, `notScopes` (v0.7.0).
- `mg_alias.json` — `{ "<canonical-slz-role>": "<customer-mg-name-or-null>" }` for all 14 roles.
- `gaps.json` — `{ gaps: [{ rule_id, severity, design_area, observed, expected, baseline_ref: {path, sha}, resource_id }] }`. The `archetype_policies_applied` matcher's `observed` snapshot includes `matched_by_defid` listing renamed assignments treated as satisfied.
- `plan.md` — grouped by design area, every bullet starts with `[rule_id: …]`.
- `scaffold.manifest.json` — `{ emitted: [{ template, bicep, params, rule_ids }] }`

## Baseline pin

`data/baseline/VERSIONS.json` records the upstream ALZ Library commit SHA.
`data/baseline/alz-library/_manifest.json` pins every vendored file's
git-blob SHA. CI job `baseline-integrity` re-hashes these on every PR.

Refresh with:

```bash
python -m slz_readiness.evaluate.vendor_baseline --force
```

## MCP servers

- `azure` (`@azure/mcp`) — uses the user's existing `az login` context.
- `sequential-thinking` — gated to `/slz-plan` and `/slz-scaffold` only.

No web-search tool is exposed to the agent.
