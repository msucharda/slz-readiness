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

Run:

```bash
az account list --query "[].{tenantId:tenantId, subscriptionId:id, subscriptionName:name}" -o json
```

Group by `tenantId` and remember the per-tenant subscription list.

## 2. Confirm the target tenant via `ask_user`

Call `ask_user` with a single-field `enum` schema. Do **not** assume the
currently-active tenant.

- Field: `tenant_id`
- Type: `string` enum
- Title: **"Which Azure tenant should Discover target?"**
- Enum values: raw `tenantId` GUIDs only (no composite labels — keeps
  parsing robust).
- Enum labels (if the host supports `enumNames` or `oneOf.title`):
  `"<tenantId> — <N> subscriptions"`. Do NOT synthesise a display name
  from the subscription `name` field — that's a subscription name, not
  a tenant name, and will mislead the operator.

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
