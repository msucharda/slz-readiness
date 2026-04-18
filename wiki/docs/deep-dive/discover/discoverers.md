# Discoverers: The Six Modules

## At a glance

| # | Module | Queries (az ...) | Emits kind |
|---|---|---|---|
| 1 | `subscription_inventory` | `account list`, `account show` | `subscription` |
| 2 | `mg_hierarchy` | `account management-group list`, `show --expand` | `management_group` |
| 3 | `policy_assignments` | `policy assignment list --scope ...` | `policy_assignment` |
| 4 | `identity_rbac` | `role assignment list --scope ...` | `role_assignment` |
| 5 | `logging_monitoring` | `monitor log-analytics workspace list` | `log_analytics_workspace` |
| 6 | `sovereignty_controls` | Composite — policy_assignments at specific scopes | `sovereignty_baseline` |

All modules live under [`scripts/slz_readiness/discover/`](https://github.com/msucharda/slz-readiness/tree/main/scripts/slz_readiness/discover).

## Shared shape

Every discoverer exposes one function:

```python
def discover(scope: RunScope) -> list[Finding]: ...
```

It reads from `scope` (tenant id + subscription ids), calls `az_common.run_az(...)` one or more times, and returns a `list[Finding]`. Errors become `error_finding`-kind Findings rather than exceptions.

## Per-module breakdown

### 1. Subscription inventory

```mermaid
flowchart LR
    S["scope.mode"]:::t -- filtered --> L1["az account show --subscription $id"]:::a
    S -- all --> L2["az account list --refresh"]:::a
    L1 --> P["one Finding per sub"]:::out
    L2 --> P
    classDef t fill:#2d333b,stroke:#30363d,color:#e6edf3;
    classDef a fill:#161b22,stroke:#6d5dfc,color:#e6edf3;
    classDef out fill:#1c2128,stroke:#3fb950,color:#e6edf3;
```

Runs first because every downstream discoverer needs a subscription id list. Cite: [`subscription_inventory.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/subscription_inventory.py).

### 2. Management-group hierarchy

Walks the tenant's MG tree. Uses `az account management-group list` + `show --expand --recurse` to build the parent/child graph. Emits one Finding per MG with `data.parent_id`, `data.children[]`, `data.subscriptions[]`. Cite: [`mg_hierarchy.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/mg_hierarchy.py).

Feeds: `mg.slz.hierarchy_shape` rule, all `archetype.*` rules (which match on MG name).

### 3. Policy assignments

Per MG and per subscription, calls `az policy assignment list --scope <scope>`. The per-scope scoping is deliberate — `--disable-scope-strict-match` would return inherited assignments and confuse the matchers.

Cite: [`policy_assignments.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/policy_assignments.py).

Feeds: `policy.*`, `sovereignty.*`, `archetype.*` rules.

### 4. Identity RBAC

Role assignments at tenant-root and per-MG scope. Filters by the built-in role ids the rules care about (Reader, Contributor, Owner, specific RBAC for Identity archetype).

Cite: [`identity_rbac.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/identity_rbac.py).

Feeds: `identity.slz.*`.

### 5. Logging & monitoring

Iterates subscriptions; `az monitor log-analytics workspace list --subscription <id>`. Emits one Finding per workspace with `data.location`, `data.sku`, `data.retention_in_days`.

Cite: [`logging_monitoring.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/logging_monitoring.py).

Feeds: `logging.slz.*` rules (including `any_subscription_has_workspace` matcher).

### 6. Sovereignty controls

Composite discoverer — does not itself make new `az` calls but **re-shapes** the outputs of `policy_assignments` at specific sovereignty-relevant scopes (tenant root and Confidential MG). Emits `sovereignty_baseline` findings whose `data.assignments[]` carries `policySetDefinitionId` values for matching.

Pinned policySetDefinitionIds for the sovereignty rules:

| Policy | ID |
|---|---|
| SLZ Global policies | `c1cbff38-87c0-4b9f-9f70-035c7a3b5523` |
| SLZ Confidential policies | `03de05a4-c324-4ccd-882f-a814ea8ab9ea` |

Cite: [`sovereignty_controls.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/sovereignty_controls.py).

Feeds: `sovereignty.slz.global_policies`, `sovereignty.slz.confidential_policies`.

## Finding → Rule fan-out

```mermaid
flowchart LR
    subgraph Disc["Discoverers"]
        direction TB
        D1["subscription_inventory"]:::d
        D2["mg_hierarchy"]:::d
        D3["policy_assignments"]:::d
        D4["identity_rbac"]:::d
        D5["logging_monitoring"]:::d
        D6["sovereignty_controls"]:::d
    end

    subgraph Rules["Rule design areas"]
        direction TB
        R1["mg (1)"]:::r
        R2["identity (1)"]:::r
        R3["logging (2)"]:::r
        R4["policy (1)"]:::r
        R5["sovereignty (2)"]:::r
        R6["archetype (8)"]:::r
    end

    D2 --> R1
    D2 --> R6
    D4 --> R2
    D5 --> R3
    D3 --> R4
    D3 --> R6
    D6 --> R5

    classDef d fill:#2d333b,stroke:#6d5dfc,color:#e6edf3;
    classDef r fill:#161b22,stroke:#30363d,color:#e6edf3;
```

<!-- Source: scripts/slz_readiness/discover/, scripts/evaluate/rules/ -->

## Why serial, not parallel

A parallel pool would be faster, especially for large tenants. Sequential execution is chosen because:

1. **Trace determinism** — NDJSON events are easier to reason about when there's only one writer.
2. **Rate-limiting** — Azure ARM limits are per-subscription + per-tenant; serialising controls the peak RPS without a token-bucket.
3. **Subscription inventory must run first** — other discoverers need its output. You'd need a two-stage parallel pipeline for marginal gain.

Profile data shows a 50-subscription tenant completes in < 3 minutes serially — acceptable for an audit tool.

## Adding a new discoverer

1. Create `scripts/slz_readiness/discover/<area>.py` exposing `def discover(scope) -> list[Finding]`.
2. Register it in the `DISCOVERERS` list at [`cli.py:24-31`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/cli.py#L24-L31).
3. Use `az_common.run_az()` for every shell-out — never raw `subprocess`.
4. Return `error_finding` for non-permission errors; skip silently for expected `not_found`.
5. Extend `tests/unit/test_discover_scope.py` if the new module introduces new CLI flags.

## Related reading

- [CLI & Scope](/deep-dive/discover/cli-and-scope) — invocation and ordering.
- [The `az` wrapper](/deep-dive/discover/az-wrapper) — `run_az` contract.
- [Rule Engine](/deep-dive/evaluate/rule-engine) — how findings become gaps.
