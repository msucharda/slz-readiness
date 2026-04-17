---
agent: slz-readiness
name: slz-evaluate
description: Run the deterministic rule engine against findings.json.
---

Invoke the **slz-evaluate** skill. Use the most recent `artifacts/<run>/findings.json`
unless the user specifies otherwise. Emit `gaps.json`. No LLM reasoning in this step.
