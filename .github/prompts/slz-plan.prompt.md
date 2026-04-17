---
agent: slz-readiness
name: slz-plan
description: Produce a prioritised, cited remediation plan from gaps.json.
---

Invoke the **slz-plan** skill. Every bullet MUST cite a `rule_id` from
`gaps.json`; uncited bullets are removed by the post-tool-use hook. Group by
design area, order by dependency (MG hierarchy first, policies after).
