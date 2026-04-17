# slz-readiness — operating instructions

These rules are **non-negotiable**. Hooks and CI enforce them; this file documents *why*.

## 1. Read-only against Azure

All `az` / Resource Graph / Azure MCP calls must use only these verbs:

```
list, show, get, query, search
```

Any call using `create`, `delete`, `set`, `update`, `apply`, `deploy`, `start`, `stop`, `restart`, `add`, `remove`, `import`, `upload`, `grant`, `revoke`, `reset`, `purge` is **blocked by `hooks/pre-tool-use.sh`**. Do not try to work around it. If you think you need a write verb, the answer is: you don't — scaffold a Bicep change instead.

## 2. Baseline is the only source of truth

- Every rule under `scripts/evaluate/rules/**/*.yml` must carry `baseline: { source, path, sha }` pointing at a file inside `data/baseline/alz-library/`.
- The `rules-resolve` CI job fails if any rule cites a file or SHA that can't be resolved locally.
- Do **not** invent rules from knowledge, documentation, or web searches. If it isn't in the vendored baseline at the pinned SHA, it isn't a rule.
- Microsoft Learn, the web, and chat history are **context**, never baseline. You may cite them in prose *in addition to* a baseline reference, never *instead of*.

## 3. Evaluate phase has zero LLM calls

`scripts/slz_readiness/evaluate/engine.py` is pure Python. Do not add LLM calls, doc-lookups, or networked queries to it. Determinism is the test: two runs over the same `findings.json` MUST produce identical `gaps.json`.

## 4. Plan phase may reason, but may not invent

When producing `plan.md`:

- Every bullet must include a `rule_id` that exists in `scripts/evaluate/rules/**/*.yml`.
- `hooks/post-tool-use.sh` suppresses any bullet that does not cite a known rule id.
- Prioritisation and grouping are allowed; adding new findings is not.

## 5. Scaffold phase fills templates, never free-forms Bicep

- Every generated `.bicep` must be produced from a template in `scripts/scaffold/avm_templates/` whose AVM module versions are pinned in `data/baseline/VERSIONS.json`.
- Parameters must validate against the JSON schema in `scripts/scaffold/param_schemas/<template>.schema.json`.
- The plugin never runs `az deployment ... create`. It emits Bicep + param files + a `how-to-deploy.md`; the human operator runs `what-if` and `create` in their pipeline.

## 6. Human-in-the-loop by default

`/slz-run` pauses between every phase for user approval. Never auto-advance unless the user explicitly passes `--no-pause`.

## 7. Log every decision

All commands run, all rule ids fired, and every template parameterisation go into `artifacts/<run>/trace.jsonl`. This file is evidence for the customer's own audit trail.

## 8. Sovereignty

v1 targets Azure **Commercial** cloud only. Do not emit guidance for Gov/China/21Vianet endpoints without an explicit scope expansion in the plan.
