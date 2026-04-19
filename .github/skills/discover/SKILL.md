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

### 1. Confirm scope with the user via `ask_user` (MANDATORY — do not skip, do not use plain text)

Every question below MUST be asked via the `ask_user` tool with a structured
schema (enum or boolean). Plain-text prompts are forbidden — they
bypass the structured-confirmation UX and caused a prior regression.

1. **Tenant enumeration** (shell):
   ```bash
   az account list --query "[].{tenantId:tenantId, subscriptionId:id, subscriptionName:name}" -o json
   az account tenant list --query "[].{tenantId:tenantId, displayName:displayName, domain:defaultDomain}" -o json
   ```
   Group subscriptions by `tenantId`. Join the second call on `tenantId`
   to obtain display names / domains; fields are frequently absent or
   `null` (guest-only access, or `account tenant list` is experimental
   and returns nulls even for some home tenants). The subscription
   `name` field is a subscription name, not a tenant display name —
   never use it AS a tenant name; it is permitted only as a clearly-
   labelled `(e.g. …)` hint at the very end of the fallback chain
   (see step 2).

   **Graph fallback.** For every tenant id where the previous calls
   yielded no usable `displayName`, call:
   ```bash
   az rest --method GET \
     --url "https://graph.microsoft.com/v1.0/tenantRelationships/findTenantInformationByTenantId(tenantId='<id>')"
   ```
   Returns `displayName` and `defaultDomainName` for any tenant in
   commercial Entra (`CrossTenantInformation.ReadBasic.All`, granted
   to all users by default). The pre-tool-use hook permits
   `az rest --method GET` against `graph.microsoft.com`. Merge the
   result into the enrichment lookup. If the call fails (network,
   scope, sovereign cloud), continue silently — the compose logic
   in step 2 has a final hint fallback.

2. **Tenant pick** — `ask_user` with field `tenant_id`, enum of raw
   `tenantId` GUIDs (values stay parser-robust). Labels (via `enumNames`
   / `oneOf.title`) compose in priority order:
   - `"<displayName> (<defaultDomain>) — <tenantId> — <N> subscriptions"`
     when both enrichment fields are present (from CLI or Graph)
   - `"<displayName> — <tenantId> — <N> subscriptions"` when only
     `displayName` is present
   - `"<tenantId> — <N> subscriptions (e.g. <sub1>, <sub2>)"` as the
     final fallback when both `az account tenant list` and
     `findTenantInformationByTenantId` returned nothing usable.
     `<sub1>`, `<sub2>` are up to two subscription names for that
     tenant from `az account list`, lexicographically sorted. The
     `(e.g. …)` prefix is mandatory — these are subscription names,
     never synthesised tenant display names.
   Title: **"Which Azure tenant should Discover target?"**. Do NOT
   assume the currently-active tenant.

3. **Scope mode** — `ask_user` with field `scope_mode`, enum
   `"all"` / `"specific"`. Title: **"Which subscription scope?"**.
   Default `"all"` but the operator must confirm explicitly. The CLI
   refuses to fan out without an explicit `--all-subscriptions` flag.

4. **If `scope_mode == "specific"`, loop**: `ask_user` enum
   `next_subscription` drawn from the chosen tenant's remaining subs,
   then `ask_user` boolean `add_another` (default `false`). Accumulate
   ids — each becomes a repeated `--subscription <id>` flag. This keeps
   the documented "one or many" behaviour that the CLI's repeatable
   flag already supports.

5. **Tenant login mismatch** — if the chosen tenant ≠ the active
   `az account show` tenant, `ask_user` boolean `acknowledged`, title
   **"Run `az login --tenant <id>` in your own shell, then acknowledge
   to continue."** The pre-tool-use hook forbids the agent from running
   `az login`; the operator does it manually.

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

### 5. Relay the Discover summary into the next `ask_user` gate
The CLI prints a one-line summary (findings count + output path) and writes
`artifacts/<run>/discover.summary.{json,md}` — a human-readable per-module
status table plus "top observations" and caveats.

Do **not** repeat the summary as a plain-text assistant message. When the
next `ask_user` gate fires (either via `/slz-run`'s phase-gate or the
operator asking what comes next), include in the form's `message` field:

1. A bounded excerpt from `discover.summary.md` — header line, per-module
   status table, and top-observations block. Keep it under ~40 lines so
   the form renders cleanly; do NOT include the full file verbatim.
2. The path `artifacts/<run>/discover.summary.md` so the operator can
   open the complete document outside the form.

Do not re-derive the numbers yourself.

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
