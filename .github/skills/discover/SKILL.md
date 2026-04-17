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
1. Confirm the active subscription and tenant with the user.
2. Create `artifacts/<run>/` where `<run>` is a UTC timestamp.
3. Run:
   ```bash
   slz-discover --out artifacts/<run>/findings.json
   ```
4. **Do not** modify any resource. All internal commands use `list` / `show` / `graph query` only.
5. Print a one-line summary: number of findings and the output path.

## Hand-off
The next skill (`slz-evaluate`) reads `findings.json`. Do **not** interpret
the findings here — evaluation is deterministic and lives outside the LLM.
