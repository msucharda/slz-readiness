---
name: slz-evaluate
description: >-
  Compare findings.json against the SHA-pinned ALZ/SLZ baseline. Deterministic,
  **no LLM reasoning** — the skill's only job is to run the Python rule engine
  and surface its output.
tools:
  - shell
---

# slz-evaluate skill

## Goal
Produce `artifacts/<run>/gaps.json`.

## Rules you MUST follow
- You do not invent rules, severities, or design-area labels. Every gap comes
  from a YAML under `scripts/evaluate/rules/` that cites a `baseline.path@sha`.
- If the engine prints `RuleLoadError`, stop and ask the user to run
  `slz-baseline-integrity` — do not attempt to guess the right SHA.

## Procedure
1. `slz-evaluate --findings artifacts/<run>/findings.json --out artifacts/<run>/gaps.json`
   (or the portable form: `python -m slz_readiness.evaluate.cli --findings … --out …`)
2. Print: total gaps, count by severity, count by design-area.
3. If zero gaps, tell the user they are compliant against the vendored baseline
   and suggest re-running after the next baseline refresh.

## Hand-off
`slz-plan` reads `gaps.json`. Do not narrate or prioritise in this skill.
