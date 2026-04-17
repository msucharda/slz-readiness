---
agent: slz-readiness
name: slz-discover
description: Run read-only discovery of the current Azure tenant.
---

Invoke the **slz-discover** skill. Confirm the active subscription before
starting, create `artifacts/<UTC-timestamp>/`, then run
`slz-discover --out artifacts/<run>/findings.json`. Do not interpret the
output — hand off to `/slz-evaluate`.
