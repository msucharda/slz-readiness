# Anti-Hallucination Contract

`slz-readiness` is designed so the LLM cannot invent best practices, rules,
severities, or Bicep. Every claim traces to a SHA-pinned file in the vendored
baseline.

## Nine guarantees

1. **SHA-pinned rules.** Every YAML under `scripts/evaluate/rules/` declares
   `baseline: { source, path, sha }`. `loaders.py` refuses to load a rule if
   the file's current git-blob SHA doesn't match.
2. **Evaluate is LLM-free.** `scripts/slz_readiness/evaluate/engine.py` is
   pure Python. Skills in `skills/evaluate/SKILL.md` explicitly forbid
   reinterpretation.
3. **az verb allowlist.** `hooks/pre-tool-use.sh` allows only
   `list|show|get|query|search`. Any `create|delete|set|update|apply|deploy`
   is blocked.
4. **Citation guard.** `hooks/post-tool-use.sh` strips any bullet from
   `plan.md` that lacks `[rule_id: X]` pointing at a rule in `gaps.json`.
5. **No free-form Bicep.** Scaffold only copies templates from
   `scripts/scaffold/avm_templates/` and validates parameters against
   `scripts/scaffold/param_schemas/*.schema.json`.
6. **No web search.** No web-search MCP / tool is wired into `apm.yml`.
7. **Auditable reasoning.** `sequential-thinking` runs with
   `DISABLE_THOUGHT_LOGGING=false` so traces are reviewable.
8. **Baseline integrity in CI.** `baseline-integrity` re-hashes every
   vendored file against `_manifest.json` on every PR.
9. **Never deploy.** The plugin only emits files. The operator runs
   `az deployment ... what-if` and `create` in their own pipeline.

## Threat model

| Threat | Mitigation |
|---|---|
| LLM invents a "best practice" not in the baseline | Evaluate is pure Python; Plan has citation guard |
| LLM writes Bicep that deviates from AVM | Scaffold only copies pinned templates |
| LLM attempts to deploy resources | pre-tool-use allowlist blocks write verbs |
| Baseline silently changes under us | `_manifest.json` + CI baseline-integrity |
| Stale rule points at a deleted baseline file | `rules_resolve` CI gate |
| Prompt injection in `az` output convinces the LLM to mutate state | Hooks block write verbs regardless of LLM intent |
