# Discover CLI & Scope Validation

## At a glance

| Attribute | Value |
|---|---|
| Entry point | `slz-discover` (pyproject.toml:31) |
| Module | [`scripts/slz_readiness/discover/cli.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/cli.py) |
| Framework | Click |
| Output | `artifacts/<run>/findings.json` |
| Discoverer count | 6 (serial execution) |
| Scope modes | `filtered` (explicit subscriptions) / `all` (tenant-wide) |

## Invocation shapes

All discover runs require `--tenant <guid>`. Scope is then disambiguated with exactly one of:

| Flag | Meaning |
|---|---|
| `--subscription <id>` (repeatable) | Enumerate only the listed subscriptions |
| `--all-subscriptions` | Enumerate every subscription the caller can see |
| (neither) | Error — no default, operator must be explicit |
| (both) | Error — mutually exclusive |

See the validation block at [`cli.py:88-154`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/cli.py#L88-L154).

## The scope state machine

```mermaid
stateDiagram-v2
    [*] --> ParsingFlags

    ParsingFlags --> MissingTenant : "no --tenant"
    MissingTenant --> ErrorExit : "UsageError"

    ParsingFlags --> NoScope : "no --subscription<br>no --all-subscriptions"
    NoScope --> ErrorExit : "UsageError<br>'pick exactly one'"

    ParsingFlags --> BothScopes : "--subscription AND --all-subscriptions"
    BothScopes --> ErrorExit : "UsageError<br>mutually exclusive"

    ParsingFlags --> Filtered : "--subscription ..."
    ParsingFlags --> AllSubs : "--all-subscriptions"

    Filtered --> ValidateIds : "check each GUID"
    ValidateIds --> ErrorExit : "malformed GUID"
    ValidateIds --> Confirmed : "all valid"

    AllSubs --> Confirmed : "no per-id check"

    Confirmed --> RunDiscoverers
    RunDiscoverers --> WriteFindings
    WriteFindings --> [*]

    ErrorExit --> [*]
```

<!-- Source: scripts/slz_readiness/discover/cli.py:88-154, tests/unit/test_discover_scope.py -->

## Why the explicit-scope requirement

A default-to-all-subscriptions posture would be convenient but dangerous — read-only calls at tenant scope can still hit rate limits, surface subscriptions the operator didn't know existed, or leak sensitive tenant topology into an artifact. Making the operator *type* `--all-subscriptions` is the lightest-weight form of informed consent the CLI can enforce.

[`tests/unit/test_discover_scope.py`](https://github.com/msucharda/slz-readiness/blob/main/tests/unit/test_discover_scope.py) covers each branch of this state machine with explicit click `Result` assertions.

## v0.4.1 — cross-tenant fan-out fix

`--all-subscriptions` initially passed `sub_filter=None` to each discoverer, which caused them to fall back to `az account list --all` and iterate every subscription visible to the caller **across every tenant they were a guest in**. The scope banner would report 10 subs while the `sovereignty_controls` progress counter reached 164 iterations (~82 cross-tenant subs × 2 sovereign assignments), violating the tenant-scope guarantee in rule §6a of the instructions.

The fix pins `sub_filter` to the tenant-scoped subscription set computed by `_list_tenant_subscriptions()`. `None` is only passed when the tenant has **zero** subscriptions, so discoverers can still emit tenant-level error findings — see [`discover/cli.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/cli.py) and the regression guard at [`tests/unit/test_discover_scope.py::test_sovereignty_controls_ignores_cross_tenant_subs`](https://github.com/msucharda/slz-readiness/blob/main/tests/unit/test_discover_scope.py).

```mermaid
flowchart LR
    A["--all-subscriptions"] --> B["_list_tenant_subscriptions(tenant)"]
    B --> C{"count"}
    C -- ">0" --> D["sub_filter = tenant_subs<br>(pinned)"]
    C -- "=0" --> E["sub_filter = None<br>(enables error findings)"]
    D --> F["Discoverers iterate<br>ONLY tenant subs"]
    E --> F
    style D fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style E fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
```

<!-- Sources: scripts/slz_readiness/discover/cli.py (sub_filter pin), tests/unit/test_discover_scope.py::test_cli_all_subscriptions_passes_none_filter, test_cli_all_subscriptions_empty_tenant_passes_none -->

## v0.5.0 — per-module summary

Alongside `findings.json`, Discover now writes `discover.summary.{json,md}` in the same run directory, capturing per-module status, top observations, and caveats (timeouts, permission errors). See [Phase Summaries](../phase-summaries.md) for the contract and [`discover/cli.py:345-444`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/cli.py#L345-L444) for the writer.

## The discoverer list

[`cli.py:24-31`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/cli.py#L24-L31) declares the ordered list of discoverers:

| Order | Module | Emits | Cite |
|---|---|---|---|
| 1 | `subscription_inventory` | `subscription` findings | [`subscription_inventory.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/subscription_inventory.py) |
| 2 | `mg_hierarchy` | `management_group` findings | [`mg_hierarchy.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/mg_hierarchy.py) |
| 3 | `policy_assignments` | `policy_assignment` findings | [`policy_assignments.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/policy_assignments.py) |
| 4 | `identity_rbac` | `role_assignment` findings | [`identity_rbac.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/identity_rbac.py) |
| 5 | `logging_monitoring` | `log_analytics_workspace` findings | [`logging_monitoring.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/logging_monitoring.py) |
| 6 | `sovereignty_controls` | `sovereignty_baseline` findings | [`sovereignty_controls.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/sovereignty_controls.py) |

Order matters only in that subscription inventory runs first to produce the subscription set that subsequent discoverers iterate.

## End-to-end flow

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operator
    participant CLI as slz-discover
    participant Trace as _trace.py
    participant Disc as DISCOVERERS loop
    participant Az as az_common.run_az
    participant Out as findings.json

    Op->>CLI: slz-discover --tenant T --all-subscriptions --run-id R
    CLI->>CLI: validate flags (cli.py:88-154)
    CLI->>Trace: set run_id ContextVar
    CLI->>Disc: iterate 6 modules
    loop per discoverer
        Disc->>Az: run_az("az ... list/show")
        Az-->>Disc: parsed JSON OR AzError
        alt success
            Disc->>Disc: emit Finding objects
        else AzError
            Disc->>Disc: emit error_finding (data.error_kind=...)
        end
        Disc->>Trace: append ndjson event
    end
    Disc-->>CLI: list[Finding]
    CLI->>Out: write artifacts/<run>/findings.json
    CLI-->>Op: path summary + exit 0
```

<!-- Source: scripts/slz_readiness/discover/cli.py, _trace.py, az_common.py -->

## Run-id and artifact layout

`--run-id` defaults to a UTC timestamp (`YYYYMMDD-HHMMSSZ`). The CLI creates `artifacts/<run-id>/` and writes:

- `findings.json` — the combined finding list
- `trace.jsonl` — NDJSON of discoverer start/stop, subprocess spawns, errors

Subsequent phases (Evaluate, Plan, Scaffold) reuse the same `<run-id>` directory so all four artifacts are co-located.

## Progress reporting

`--progress` (default on when stderr is a TTY) pipes through [`_progress.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/_progress.py) which prints a compact `[3/6] policy_assignments …` line. In CI it's silenced by default to keep logs readable.

## Failure semantics

```mermaid
flowchart LR
    Start["discoverer starts"]:::t
    Ok["all az calls succeed"]:::ok
    One["one az call fails<br>(AzError)"]:::warn
    All["every az call fails"]:::bad

    Start --> Ok
    Start --> One
    Start --> All

    Ok --> Emit["emit domain findings"]:::t
    One --> EmitBoth["emit domain findings<br>+ error_finding"]:::t
    All --> EmitErr["emit error_finding only<br>(still exit 0)"]:::t

    EmitBoth --> Next["next discoverer runs"]:::t
    EmitErr --> Next
    Emit --> Next

    classDef t fill:#2d333b,stroke:#30363d,color:#e6edf3;
    classDef ok fill:#1c2128,stroke:#3fb950,color:#e6edf3;
    classDef warn fill:#1c2128,stroke:#d29922,color:#e6edf3;
    classDef bad fill:#1c2128,stroke:#f85149,color:#e6edf3;
```

Discoverer failures are **findings, not exceptions.** Evaluate turns an `error_finding` into a `Gap(status=unknown)`. Scaffold skips unknowns. The operator sees a plan bullet saying "could not observe — permission denied at scope X" instead of a silent missing gap.

## Related reading

- [Discoverers](/deep-dive/discover/discoverers) — the 6 modules in detail.
- [The `az` wrapper](/deep-dive/discover/az-wrapper) — subprocess, trace, classification.
- [Rule Engine](/deep-dive/evaluate/rule-engine) — how error findings become unknown gaps.
