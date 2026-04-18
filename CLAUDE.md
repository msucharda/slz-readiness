# CLAUDE.md

Alias for [`AGENTS.md`](AGENTS.md). Read that file first — it contains the authoritative coding-agent context for this repository.

## Claude-specific pointers

- **Do not** use the `write_to_file` tool on anything under `data/baseline/alz-library/` — that directory is vendored content verified by CI hashes. To update it, run `python -m slz_readiness.evaluate.vendor_baseline --sha <new-sha>`.
- **Do not** attempt to run `az deployment … create` or `… delete` from within the agent — the pre-tool-use hook will block it, and it should. HITL is the contract.
- **Do not** invent rule ids in plan-phase output. The post-tool-use hook silently moves uncited bullets to `plan.dropped.md`, which is loud-failure by design.
- When adding a rule, cross-check:
  - `MATCHERS` in `scripts/slz_readiness/evaluate/matchers.py` (matcher kind must exist)
  - `ALLOWED_TEMPLATES` in `scripts/slz_readiness/scaffold/template_registry.py` (template must exist)
  - Vendored baseline has the referenced file at the pinned SHA
