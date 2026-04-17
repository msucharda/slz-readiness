# Architecture

`slz-readiness` is a Copilot plugin that audits an Azure tenant for
**Sovereign Landing Zone (SLZ)** readiness against a **vendored, SHA-pinned**
Cloud Adoption Framework baseline.

## Four deterministic phases

```
┌─────────────┐  findings.json  ┌──────────────┐  gaps.json  ┌──────────┐  plan.md   ┌──────────────┐
│  Discover   │ ───────────────▶│  Evaluate    │────────────▶│  Plan    │───────────▶│  Scaffold    │
│ (az, MCP)   │                 │ (pure py)    │             │ (LLM +   │            │ (AVM Bicep)  │
│ read-only   │                 │ NO LLM       │             │  SeqTh)  │            │ templates    │
└─────────────┘                 └──────────────┘             └──────────┘            └──────────────┘
```

- **Discover** (`skills/discover`, `scripts/slz_readiness/discover/`) — runs
  `az ... list|show|query` and emits `findings.json`.
- **Evaluate** (`skills/evaluate`, `scripts/slz_readiness/evaluate/`) — pure
  Python. Each rule YAML under `scripts/evaluate/rules/` declares a
  `baseline.path@sha`. If the file's SHA drifts, the rule refuses to load.
- **Plan** (`skills/plan`) — LLM + `sequential-thinking` narrates the gaps.
  Every bullet must cite a `rule_id`; the post-tool-use hook strips any bullet
  that doesn't.
- **Scaffold** (`skills/scaffold`, `scripts/slz_readiness/scaffold/`) — fills
  the pinned AVM Bicep templates under `scripts/scaffold/avm_templates/`.
  Free-form Bicep is not possible — parameters validate against JSON schemas.

## Data contracts

- `findings.json` — `{ findings: [{ resource_type, resource_id, scope, observed_state, query_cmd }] }`
- `gaps.json` — `{ gaps: [{ rule_id, severity, design_area, observed, expected, baseline_ref: {path, sha}, resource_id }] }`
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
