---
name: slz-plan
description: >-
  Narrate and prioritise the gaps in gaps.json into a human-readable remediation
  plan. Uses sequential-thinking for structured reasoning. **Every bullet must
  cite a rule_id that exists in gaps.json — uncited bullets are stripped by the
  post-tool-use hook.**
tools:
  - shell
  - sequential-thinking
---

# slz-plan skill

## Goal
Produce `artifacts/<run>/plan.md` and `plan.json`.

## Hard rules
- **Cite rule_ids.** Each recommendation bullet must start with
  `- [rule_id: X] …`. The post-tool-use hook deletes any bullet without a
  `rule_id` matching a rule in `gaps.json`.
- **No new best practices.** Only rephrase, group, and order what evaluate
  already found. Do not add tenants-wide "nice-to-haves".
- **No execution.** You never run `az` write verbs. The plugin ships only
  scaffolding; deployment is the operator's job.

## Procedure
1. Load `gaps.json`. Group gaps by `design_area`.
2. For each group, use the `sequential-thinking` tool to order gaps by
   dependency (e.g. create MG hierarchy before assigning policies).
3. Write `plan.md`:
   - Intro (one paragraph, no new rules).
   - One H2 per design_area.
   - Ordered bullets, each citing `rule_id` and the `baseline_ref` path@sha.
4. Write `plan.json`: same content, structured.

## Hand-off
`slz-scaffold` reads `gaps.json` + `plan.json` and emits Bicep templates.
