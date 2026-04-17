---
name: slz-discover
description: >-
  Run read-only discovery against the user's Azure tenant. Emits findings.json
  recording every management-group, policy assignment, role assignment, and
  Log Analytics workspace observed, plus the exact `az` command used.
tools:
  - shell
  - azure
---

# slz-discover skill

## Goal
Produce `artifacts/<run>/findings.json` for the current tenant.

## Preconditions
- The user is logged in (`az account show` succeeds).
- The repository is installed: `pip install -e .`
- The pre-tool-use hook is enabled (enforces read-only verbs).

## Procedure

### 1. Confirm scope with the user (MANDATORY — do not skip)

Before any discovery runs, the agent **must** interactively confirm:

1. **Tenant**: exactly one Azure tenant id. Enumerate the tenants visible
   to the user (shell):
   ```bash
   az account list --query "[].{tenantId:tenantId, subName:name}" -o json
   ```
   Group by `tenantId`, print a numbered list, and ask which tenant to target.
   Do **not** assume the currently-active tenant — a user may have multiple
   tenant memberships and want to target a non-default one.

2. **Subscription scope**: the user picks **one** of:
   - A specific set of subscription ids (one or many), *or*
   - All subscriptions in the tenant.

   Default recommendation is **all**, but the user must confirm it explicitly.
   The CLI refuses to fan out without an explicit `--all-subscriptions` flag.

3. **Tenant login**: if the chosen tenant ≠ the active `az account show`
   tenant, ask the user to run `az login --tenant <id>` in their shell. The
   pre-tool-use hook does not currently allow `az login` to be run by the
   agent; the user runs it manually. Wait for them to confirm before
   proceeding.

### 2. Create the run directory
`artifacts/<run>/` where `<run>` is a UTC timestamp.

### 3. Invoke the CLI with explicit scope flags
```bash
python -m slz_readiness.discover.cli \
    --out artifacts/<run>/findings.json \
    --tenant <tenant-id> \
    (--subscription <id> [--subscription <id> ...] | --all-subscriptions)
```

`slz-discover` on PATH works identically if the package is `pip install -e .`'d.

The CLI **validates** that:
- `--tenant` matches the active `az account show` tenant (otherwise exits with
  a message telling the user to run `az login --tenant <id>`).
- Exactly one of `--subscription` or `--all-subscriptions` is given.

### 4. Do **not** modify any resource.
All internal commands use `list` / `show` / `graph query` only.

### 5. Print a one-line summary
Number of findings and the output path (the CLI does this already). The CLI
also writes `artifacts/<run>/discover.summary.{json,md}` — a human-readable
per-module status table plus "top observations" and caveats. **Read
`discover.summary.md` and relay it verbatim** to the user before handing off
to evaluate; do not re-derive the numbers.

## Scope metadata

Every run persists its chosen scope into `findings.json`:

```json
{
  "run_scope": {
    "tenant_id": "...",
    "mode": "all" | "filtered",
    "subscription_ids": ["..."]
  },
  "findings": [...]
}
```

`trace.jsonl` gets a `run.scope` event with the same fields. Evaluate reads
`findings` and ignores `run_scope`; the audit trail uses it.

## Progress feedback
Discovery is serial and can take 10+ minutes on large tenants (one
`policy state list` call per subscription × sovereignty assignment is the
long tail). The CLI emits live progress so a long run is not mistaken for a
hang:

- **Per-stage** lines on **stderr** as each module starts and ends:
  `▶ <module> ...` then `✓ <module> — N findings in T.Ts` (or `✗ ... — error`).
- **Intra-stage** lines for long stages (`sovereignty_controls`,
  `policy_assignments`, `identity_rbac`): `[i/N] <label>`. Carriage-return
  overwrite on a TTY; one line per ~10% bucket otherwise.
- **Per-stage artifacts** appear in `artifacts/<run>/stages/<module>.json`
  as soon as each stage completes. Inspect them mid-run to see partial
  results without waiting for the final `findings.json`.
- **Trace** at `artifacts/<run>/trace.jsonl` remains the canonical evidence
  log (`tail -f` it for the most granular signal).

## Hand-off
The next skill (`slz-evaluate`) reads the final `findings.json`. Do **not**
interpret the findings here — evaluation is deterministic and lives outside
the LLM. The per-stage `stages/*.json` files are debug artifacts only;
evaluate ignores them.
