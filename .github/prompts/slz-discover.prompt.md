---
agent: slz-readiness
name: slz-discover
description: Run read-only discovery of an Azure tenant (scope confirmed via ask_user).
---

Invoke the **slz-discover** skill. **Never ask the operator anything via plain
text** — every question below MUST be asked via the `ask_user` tool with the
schema specified. The instructions file §6a is the contract; this prompt
spells it out at the slash-command level because the most-specific context
wins.

## 1. Enumerate visible tenants and subscriptions

Run the subscription list:

```bash
az account list --query "[].{tenantId:tenantId, subscriptionId:id, subscriptionName:name}" -o json
```

Then enrich with tenant display names (best-effort — fields may be missing
for guest-only access):

```bash
az account tenant list --query "[].{tenantId:tenantId, displayName:displayName, domain:defaultDomain}" -o json
```

Group subscriptions by `tenantId` and remember the per-tenant subscription
list. Build a lookup from `tenantId` → `(displayName, defaultDomain)` from
the second call; entries may be absent (or have `null` fields) for tenants
the caller is only a guest in, and even some home tenants — `az account
tenant list` is an experimental command that frequently returns nulls.

**Graph fallback (per tenant id where step above yielded no usable
`displayName`).** Microsoft Graph exposes basic tenant info to any
authenticated user via the
`CrossTenantInformation.ReadBasic.All` scope (granted by default in
Entra). For each tenant id missing a `displayName`, call:

```bash
az rest --method GET \
  --url "https://graph.microsoft.com/v1.0/tenantRelationships/findTenantInformationByTenantId(tenantId='<id>')"
```

The response shape is `{ "displayName": "...", "defaultDomainName":
"...", "tenantId": "...", "federationBrandName": null }`. Merge
`displayName` and `defaultDomainName` into the lookup. The pre-tool-use
hook permits `az rest --method GET` against `graph.microsoft.com`. If
the call fails (network, scope, sovereign cloud), record the failure on
stderr and continue — the next-step compose logic handles the missing
case.

## 2. Confirm the target tenant via `ask_user`

Call `ask_user` with a single-field `enum` schema. Do **not** assume the
currently-active tenant.

- Field: `tenant_id`
- Type: `string` enum
- Title: **"Which Azure tenant should Discover target?"**
- Enum values: raw `tenantId` GUIDs only (values stay parser-robust).
- Enum labels (via `enumNames` or `oneOf.title`): compose from the
  enrichment lookup, in priority order:
  - If both `displayName` and `defaultDomain` are present (from either
    `az account tenant list` or the Graph fallback):
    `"<displayName> (<defaultDomain>) — <tenantId> — <N> subscriptions"`
  - If only `displayName` is present:
    `"<displayName> — <tenantId> — <N> subscriptions"`
  - Otherwise — final fallback when both `az account tenant list` and
    `findTenantInformationByTenantId` failed to yield a `displayName` —
    emit a **disambiguating subscription-name hint** of the form:
    `"<tenantId> — <N> subscriptions (e.g. <sub1>, <sub2>)"`
    where `<sub1>`, `<sub2>` are up to two subscription names from
    `az account list` for that tenant, lexicographically sorted for
    stability. The `(e.g. …)` prefix is mandatory — it makes clear
    these are subscription names, NOT a synthesised tenant display
    name. If the tenant has only one subscription, list that one;
    if zero (shouldn't happen — every visible tenant has at least one
    subscription in `az account list`), drop the parenthetical.

## 3. Confirm scope mode via `ask_user`

Call `ask_user` again with:

- Field: `scope_mode`
- Type: `string` enum
- Title: **"Which subscription scope?"**
- Enum values: `"all"`, `"specific"`.
- Default: `"all"`, but the operator must confirm explicitly.

## 4. If `scope_mode == "specific"`, iterate to collect the subscription set

§6a permits "one or many" subscription ids and the CLI's `--subscription`
flag is repeatable. Do NOT collapse the set to a single pick.

Loop:

1. Call `ask_user` with a single-field `enum` schema:
   - Field: `next_subscription`
   - Type: `string` enum
   - Title: **"Add a subscription to the scope."**
   - Enum values: every subscription id in the chosen tenant that hasn't
     been selected yet.
   - Enum labels: `"<subscriptionId> — <subscriptionName>"`.
2. Append the chosen id to the running list.
3. Call `ask_user` with a single-field `boolean` schema:
   - Field: `add_another`
   - Title: **"Add another subscription?"**
   - Default: `false`.
4. If `add_another` is true and there are unselected subs remaining, loop.
   Otherwise stop.

## 5. If the chosen tenant differs from `az account show` tenant

The pre-tool-use hook forbids the agent from running `az login`. Call
`ask_user` with:

- Field: `acknowledged`
- Type: `boolean`
- Title: **"Run `az login --tenant <id>` in your own shell, then
  acknowledge to continue."**

Wait for `acknowledged == true` before proceeding.

## 6. Create the run directory and invoke the CLI

```bash
mkdir -p artifacts/<UTC-timestamp>
slz-discover --out artifacts/<run>/findings.json \
  --tenant <tenant-id> \
  (--all-subscriptions | --subscription <id> [--subscription <id> ...])
```

(Portable form: `python -m slz_readiness.discover.cli …` with the same
flags.) The CLI refuses to run without `--tenant` and exactly one of
`--subscription` / `--all-subscriptions` — this is a guard-rail, not a
suggestion.

## 7. Relay the Discover summary to the operator

The CLI writes `artifacts/<run>/discover.summary.md`. Do NOT repeat it as
a plain-text assistant message. Instead, when `/slz-run` or the operator
asks whether to continue to Evaluate, the follow-up `ask_user` gate's
`message` field MUST include:

1. A short excerpt: the header line, module status table, and top
   observations block from `discover.summary.md`. Keep it under ~40
   lines to avoid form rendering issues.
2. The path `artifacts/<run>/discover.summary.md` for the operator to
   open if they want the full document (it contains per-module detail
   and may exceed what renders cleanly in a form).

Do not interpret the findings yourself — hand off to `/slz-evaluate`.

## 8. Brownfield advisory — include in the next-phase gate `message`

**If the discovered tenant clearly already operates a landing zone**
(e.g. `present_ids` contains MGs that are not the SLZ canonical names
but look like production/platform/hub/spoke), append the following
paragraph to the next `ask_user` gate's `message` field so the
operator is forewarned:

> ⚠ **Brownfield advisory.** This tenant appears to already operate a
> landing zone under non-canonical MG names. Run `/slz-reconcile` next
> to map the canonical SLZ roles (corp, online, platform, …) to your
> tenant's actual MG names. Discover and Evaluate both consume the
> resulting `mg_alias.json` to retarget probes and selectors against
> your real MGs. Scaffold surfaces the same alias mapping as a
> substitution table in `how-to-deploy.md` — the emitted `.bicep`
> files keep canonical role names so they stay reusable across
> tenants; you (or your pipeline) substitute `MG_ID` per template at
> deploy time. See `docs/brownfield.md` for the full retargeting
> workflow and the v0.8.0 roadmap for in-place Bicep rewriting.

Do not suppress gaps on the basis of this advisory — the evaluator
still runs as normal. The advisory exists so the operator reads the
gap list in the right light.
