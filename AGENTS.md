# AGENTS.md — Repository root

Context file for coding agents working in `slz-readiness`.

## What this repo is

A GitHub Copilot CLI plugin that audits Azure tenants against the Azure/Microsoft **Sovereign Landing Zone** baseline. Four-phase pipeline: **Discover → Evaluate → Plan → Scaffold**. Read-only against Azure. Deterministic in Evaluate and Scaffold. LLM narration only in Plan, behind a citation guard.

Canonical version source: `apm.yml` (currently `0.4.0`).

## Build & test

```bash
# dev install
pip install -e .[dev]

# lint
ruff check .
mypy scripts/slz_readiness

# test (unit only — no live Azure)
pytest -q

# what-if a specific Bicep template
bicep build scripts/scaffold/avm_templates/<name>.bicep
```

Three-OS CI matrix (Linux/macOS/Windows); changes touching `discover/az_common.py` must pass all three.

## Where things live

| Path | What |
|---|---|
| `apm.yml`, `.github/plugin/plugin.json` | Plugin manifests (keep in lockstep) |
| `.github/agents/`, `.github/skills/`, `.github/prompts/` | Plugin surface |
| `hooks/` | Pre/post tool-use guards |
| `scripts/slz_readiness/` | Python package (discover, evaluate, plan, scaffold; shared `_summary.py`) |
| `scripts/evaluate/rules/` | 14 rule YAMLs (add rules here, YAML-only) |
| `scripts/scaffold/avm_templates/` | 7 Bicep templates + param schemas |
| `data/baseline/alz-library/` | Vendored ALZ Library (pinned SHA) — do NOT hand-edit |
| `tests/` | Unit + golden tests |
| `docs/` | First-party architecture notes |
| `wiki/` | Generated wiki (VitePress) |

## Non-negotiable rules

1. **Read-only Azure.** Only shell out to `az list|show|get|query|search|describe|export|version|account`. Never `create|delete|set|update|apply|deploy|assign|invoke|new|put|patch`. Enforced by [`hooks/pre_tool_use.py`](hooks/pre_tool_use.py).
2. **Baseline is truth.** Every rule's `baseline_ref` must point at a vendored file at a pinned SHA. CI verifies.
3. **Evaluate is deterministic.** Zero LLM calls in `evaluate/`. Sort output by `(rule_id, resource_id)`. Golden-tested.
4. **Plan bullets must cite `rule_id:<real-id>`.** Enforced by [`hooks/post_tool_use.py`](hooks/post_tool_use.py).
5. **Scaffold uses only `ALLOWED_TEMPLATES`.** No free-form Bicep generation.
6. **Policies ship Audit-first.** Scaffold defaults `rolloutPhase=audit` and rewrites baseline `Deny` effects to `Audit` at emit-time. Operators must explicitly opt into `rolloutPhase=enforce` for Wave 2 after observation. See `how-to-deploy.md` emitted per run.
7. **HITL for deployment.** `az deployment … create` is agent-blocked. Operators run it manually after `what-if`.
8. **Scope confirmation.** `--tenant` is always required; `--subscription` and `--all-subscriptions` are mutually exclusive and one is required.
9. **Everything traced.** `_trace.py` NDJSON in `artifacts/<run>/trace.jsonl`.

## Adding a rule (YAML-only path)

1. Create `scripts/evaluate/rules/<area>/<rule_id>.yml`.
2. Use an existing matcher `kind` (see [`matchers.py:98`](scripts/slz_readiness/evaluate/matchers.py)).
3. Set `baseline_ref` to an existing vendored path + the pinned SHA.
4. Add a row to `RULE_TO_TEMPLATE` in [`template_registry.py:21`](scripts/slz_readiness/scaffold/template_registry.py) pointing at an existing template.
5. Regenerate golden fixtures and run `pytest -q`.

## Release

All four version strings are bumped in lockstep by `python scripts/release.py --bump <level>`: `apm.yml`, `.github/plugin/plugin.json`, `scripts/slz_readiness/__init__.py`, `data/baseline/VERSIONS.json`. CI (`release.yml`) rejects drift.

## Boundaries — do NOT touch without explicit request

- `.github/` structure (agent/skills/prompts/hooks contract)
- `data/baseline/alz-library/` content or `_manifest.json`
- `hooks/*.py` safety logic (add tests before loosening)
- `scripts/slz_readiness/evaluate/engine.py` determinism invariants

## Wiki

Generated under `wiki/docs/`. Entry point: [`wiki/docs/.vitepress/config.mts`](wiki/docs/.vitepress/config.mts). Author/edit source pages in Markdown; do not commit `node_modules/` or `dist/`.
