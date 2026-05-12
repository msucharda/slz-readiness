# Anti-Hallucination Contract

`slz-readiness` is designed so the LLM cannot invent best practices, rules,
severities, or Bicep. Every claim traces to a SHA-pinned file in the vendored
baseline.

## Ten guarantees

1. **SHA-pinned rules.** Every YAML under `scripts/evaluate/rules/` declares
   `baseline: { source, path, sha }`. `loaders.py` refuses to load a rule if
   the file's current git-blob SHA doesn't match.
2. **Evaluate is LLM-free.** `scripts/slz_readiness/evaluate/engine.py` is
   pure Python. Skills in `skills/evaluate/SKILL.md` explicitly forbid
   reinterpretation.
3. **Reconcile is schema-gated.** `slz-reconcile` writes `mg_alias.json` only
   after validating canonical roles, uniqueness, and membership in discovered
   management groups; the post hook repairs direct file-write drift.
4. **az verb allowlist.** `hooks/pre_tool_use.py` allows read-only Azure verbs
   and blocks write verbs such as `create`, `delete`, `set`, `update`,
   `apply`, `deploy`, `assign`, `invoke`, `put`, and `patch`.
5. **Citation guard.** `hooks/post_tool_use.py` strips any bullet from
   `plan.md` that lacks a `(rule_id: X)` citation pointing at a known rule.
6. **No free-form Bicep.** Scaffold only copies templates from
   `scripts/scaffold/avm_templates/` and validates parameters against
   `scripts/scaffold/param_schemas/*.schema.json`.
7. **No web search.** No web-search MCP / tool is wired into `apm.yml`.
8. **Auditable reasoning.** `sequential-thinking` runs with
   `DISABLE_THOUGHT_LOGGING=false` so traces are reviewable.
9. **Baseline integrity in CI.** `baseline-integrity` re-hashes every
   vendored file against `_manifest.json` on every PR.
10. **Never deploy.** The plugin only emits files. The operator runs
    `az deployment ... what-if` and `create` in their own pipeline. Optional
    runbooks are emitted only for the operator and are blocked from agent
    execution by `hooks/pre_tool_use.py`.

## Threat model

| Threat | Mitigation |
|---|---|
| LLM invents a "best practice" not in the baseline | Evaluate is pure Python; Plan has citation guard |
| LLM maps brownfield management groups incorrectly | Reconcile requires per-role operator confirmation plus schema validation |
| LLM writes Bicep that deviates from AVM | Scaffold only copies pinned templates |
| LLM attempts to deploy resources | pre-tool-use allowlist blocks write verbs |
| Baseline silently changes under us | `_manifest.json` + CI baseline-integrity |
| Stale rule points at a deleted baseline file | `rules_resolve` CI gate |
| Prompt injection in `az` output convinces the LLM to mutate state | Hooks block write verbs regardless of LLM intent |
