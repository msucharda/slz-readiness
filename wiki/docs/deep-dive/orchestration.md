# Orchestration

## At a glance

| Attribute | Value |
|---|---|
| Entry point | `/slz-run` slash command |
| Prompt | [`.github/prompts/slz-run.prompt.md`](https://github.com/msucharda/slz-readiness/blob/main/.github/prompts/slz-run.prompt.md) |
| Nature | **Prompt, not skill.** Runs under the agent's default skill context. |
| Phases | Discover → Evaluate → Plan → Scaffold |
| HITL pauses | Between each phase (suppressible with `--no-pause`) |

> There is **no** `.github/skills/run/SKILL.md`. Each individual phase has a skill; the orchestration lives in a prompt. Documentation or tooling that references a "run skill" is incorrect — the file does not exist.

## The orchestration loop

```mermaid
stateDiagram-v2
    [*] --> Init

    Init --> Discover : "load slz-run prompt<br>parse --tenant, scope flags"
    Discover --> PauseD : "findings.json written"
    PauseD --> Evaluate : "operator continues"
    PauseD --> Exit : "operator stops"

    Evaluate --> PauseE : "gaps.json written"
    PauseE --> Plan : "operator continues"
    PauseE --> Exit : "operator stops"

    Plan --> PauseP : "plan.md written"
    PauseP --> Scaffold : "operator continues"
    PauseP --> Exit : "operator stops"

    Scaffold --> Summary : "bicep/ written"
    Summary --> [*]
    Exit --> [*]
```

Each pause is a plain user-prompt turn — the CLI waits for the operator to type `continue` (or equivalent) before invoking the next skill. `--no-pause` makes the orchestrator proceed automatically through all four phases.

## Why pauses are default

- **Discover** can surface surprises (hidden subscriptions, permission gaps). Worth a look before spending time on Evaluate.
- **Evaluate** produces the first actionable artifact (`gaps.json`). Operators often stop here for compliance reporting.
- **Plan** is the narrative layer — last chance to confirm "the machine's interpretation matches our understanding" before Scaffold.
- **Scaffold** produces Bicep. Scaffold failure (skipped rules, unknown gaps) is visible here before a `what-if` is run.

## Flag propagation

```mermaid
flowchart LR
    Op["Operator"]:::op
    Orch["slz-run prompt"]:::o

    subgraph Flags["Flags forwarded verbatim"]
        F1["--tenant"]:::f
        F2["--subscription / --all-subscriptions"]:::f
        F3["--run-id"]:::f
        F4["--out-dir"]:::f
    end

    subgraph Local["slz-run-only flags"]
        L1["--no-pause"]:::lf
        L2["--stop-after=discover|evaluate|plan"]:::lf
    end

    Op --> Orch
    Orch --> F1 & F2 & F3 & F4 --> Phases["each phase CLI"]:::p
    L1 -.-> Orch
    L2 -.-> Orch

    classDef op fill:#2d333b,stroke:#30363d,color:#e6edf3;
    classDef o fill:#2d333b,stroke:#6d5dfc,color:#e6edf3;
    classDef f fill:#161b22,stroke:#30363d,color:#e6edf3;
    classDef lf fill:#161b22,stroke:#d29922,color:#e6edf3;
    classDef p fill:#1c2128,stroke:#3fb950,color:#e6edf3;
```

The orchestrator does not invent arguments. It passes `--tenant`, scope flags, and `--run-id` through unchanged, ensuring all four artifacts land in one `artifacts/<run-id>/` directory.

## Failure propagation

A phase that exits non-zero stops the chain — subsequent phases are not invoked. The artifacts from successful earlier phases remain on disk. Re-running `/slz-run` with the same `--run-id` resumes at the first phase whose output is missing or stale.

Caveat: the orchestrator checks for *file existence*, not *content freshness*. If you edit `findings.json` by hand, re-run will not re-detect and not re-discover. Use a fresh `--run-id` to force a clean pass.

## Cross-cutting: tracing

Every phase writes to the same `artifacts/<run-id>/trace.jsonl`. Reading that file chronologically is the best single-view debugging tool:

```json
{"ts":"...","run":"R","event":"discover.start"}
{"ts":"...","run":"R","event":"az.start","args":["account","list"]}
{"ts":"...","run":"R","event":"az.end","status":"ok","dur_ms":423}
{"ts":"...","run":"R","event":"discover.end","findings":217}
{"ts":"...","run":"R","event":"evaluate.start"}
{"ts":"...","run":"R","event":"evaluate.end","gaps":11}
{"ts":"...","run":"R","event":"plan.start"}
{"ts":"...","run":"R","event":"plan.dropped","bullet":"..."}
{"ts":"...","run":"R","event":"plan.end"}
{"ts":"...","run":"R","event":"scaffold.start"}
{"ts":"...","run":"R","event":"scaffold.skipped","rule_id":"archetype.alz_decommissioned.policies","reason":"no RULE_TO_TEMPLATE entry"}
{"ts":"...","run":"R","event":"scaffold.end","emitted":8}
```

## What `/slz-run` does NOT do

- It doesn't deploy. `az deployment … create` is explicitly blocked — see [Hooks](/deep-dive/hooks).
- It doesn't clean up old artifact directories. Operators manage retention.
- It doesn't parallelise phases. Each runs to completion before the next starts.
- It doesn't retry on failure. An operator inspects the trace, fixes the issue, and re-runs.

## Related reading

- [Plugin Mechanics](/deep-dive/plugin-mechanics) — how the prompt is registered.
- [Discover CLI & Scope](/deep-dive/discover/cli-and-scope) — the first phase in detail.
- [Plan](/deep-dive/plan) — the narrative phase.
- [Scaffold: Engine & Registry](/deep-dive/scaffold/engine-and-registry) — the last phase.
