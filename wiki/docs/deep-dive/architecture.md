# Architecture Overview

## At a glance

| Attribute | Value |
|---|---|
| Architecture style | 4-phase unidirectional pipeline |
| Data flow | Azure → `findings.json` → `gaps.json` → `plan.md` + `bicep/*.bicep` |
| Safety model | Shell-level verb allowlist + citation guard + template closed-set |
| Determinism | Discover/Evaluate/Scaffold = deterministic; Plan = LLM-narrated + citation-filtered |
| Human-readable numbers | Every phase emits `<phase>.summary.{json,md}`; Scaffold rolls them up into `run.summary.md` — see [Phase Summaries](./phase-summaries.md) |
| Plugin host | GitHub Copilot CLI (APM format) |
| Source-of-truth | Azure Landing Zones Library, pinned by git SHA |

## The pipeline

```mermaid
flowchart LR
    subgraph ExtTop["External"]
        Az["Azure ARM / Graph"]:::ext
        ALZ["ALZ Library<br>(pinned SHA)"]:::ext
    end

    subgraph Plugin["slz-readiness plugin"]
        direction LR
        D["1 · Discover<br>6 modules"]:::p
        E["2 · Evaluate<br>engine.py · 14 rules"]:::p
        P["3 · Plan<br>LLM + citation guard"]:::p
        S["4 · Scaffold<br>template registry"]:::p
    end

    subgraph Arts["artifacts/&lt;run&gt;/"]
        direction LR
        F["findings.json"]:::a
        G["gaps.json"]:::a
        PL["plan.md"]:::a
        B["bicep/*.bicep"]:::a
        T["trace.jsonl"]:::a
    end

    User["Operator"] --> D
    D -- "az list/show/get" --> Az
    D --> F --> E
    ALZ --> E
    E --> G
    G --> P --> PL
    G --> S --> B
    D --> T
    E --> T
    P --> T
    S --> T

    PL --> Human["Operator reviews"]:::human
    B --> Human
    Human --> Deploy["az deployment what-if / create"]:::human

    classDef ext fill:#161b22,stroke:#30363d,color:#8b949e;
    classDef p fill:#2d333b,stroke:#6d5dfc,color:#e6edf3;
    classDef a fill:#161b22,stroke:#30363d,color:#e6edf3;
    classDef human fill:#1c2128,stroke:#3fb950,color:#e6edf3;
```

<!-- Source: docs/architecture.md, .github/agents/slz-readiness.agent.md, scripts/slz_readiness/ -->

## The three invariants

Everything else is implementation detail of these three:

1. **Read-only against Azure.** The shell-level [`hooks/pre_tool_use.py:21`](https://github.com/msucharda/slz-readiness/blob/main/hooks/pre_tool_use.py#L21) `ALLOW_RE` admits `list|show|get|query|search|describe|export|version|account`, and `DENY_RE` blocks `create|delete|set|update|apply|deploy|assign|invoke|new|put|patch`. Gated to `az|azd|bicep` (`AZURE_TOOL_RE`).
2. **Baseline is truth.** Every rule's [`baseline_ref`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/evaluate/models.py) points at a file at a git SHA from [Azure-Landing-Zones-Library](https://github.com/Azure/Azure-Landing-Zones-Library). The baseline is vendored at [`data/baseline/alz-library/`](https://github.com/msucharda/slz-readiness/tree/main/data/baseline/alz-library), every blob's SHA recorded in `_manifest.json`, re-verified by CI ([`baseline_integrity.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/evaluate/baseline_integrity.py)).
3. **Deterministic Evaluate.** [`engine.py:51-140`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/evaluate/engine.py#L51-L140) is pure Python. Output is sorted by `(rule_id, resource_id)`. Zero LLM calls. Tested by [`tests/unit/test_evaluate_golden.py`](https://github.com/msucharda/slz-readiness/blob/main/tests/unit/test_evaluate_golden.py).

## Data contracts

```mermaid
classDiagram
    class RunScope {
        +tenant_id: str
        +mode: "filtered" | "all"
        +subscription_ids: list~str~
    }

    class Finding {
        +kind: str
        +scope: str
        +data: dict
    }

    class ErrorFinding {
        +kind == "error_finding"
        +data.error_kind: str
    }

    class Rule {
        +rule_id: str
        +design_area: str
        +severity: str
        +target: Target
        +matcher: MatcherSpec
        +baseline_ref: BaselineRef
        +remediation: Remediation
    }

    class Gap {
        +rule_id: str
        +severity: str
        +resource_id: str
        +status: str
        +baseline_ref: BaselineRef
        +observed: dict
    }

    class BicepEmission {
        +rule_id: str
        +template: str
        +scope: str | null
        +bicep: path
        +params: path
    }

    RunScope ..> Finding
    Finding <|-- ErrorFinding
    Finding ..> Rule : "evaluated against"
    Rule ..> Gap : "produces"
    Gap ..> BicepEmission : "drives"
```

<!-- Source: scripts/slz_readiness/evaluate/models.py, loaders.py, scaffold/engine.py -->

## Cross-cutting concerns

### Tracing

[`_trace.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/_trace.py) uses a `ContextVar` so a run-id propagates into every subprocess spawn and evaluate pass without explicit threading. NDJSON appending means even partial runs leave useful artifacts.

### Error classification

[`az_common.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/discover/az_common.py) classifies every `az` failure into one of four `AzError.kind` values. Discover turns each into an `error_finding`; Evaluate turns each into `status=unknown`; Scaffold refuses to emit for `unknown`.

```mermaid
sequenceDiagram
    autonumber
    participant Discover
    participant AzWrap as az_common.run_az
    participant Az as az CLI
    participant Evaluate
    participant Scaffold

    Discover->>AzWrap: args
    AzWrap->>Az: spawn subprocess
    alt returns 0
        Az-->>AzWrap: stdout
        AzWrap-->>Discover: parsed JSON
    else non-zero
        Az-->>AzWrap: stderr
        AzWrap->>AzWrap: classify (permission_denied/not_found/rate_limited/network)
        AzWrap-->>Discover: raise AzError
        Discover->>Discover: append error_finding
    end
    Discover->>Evaluate: findings (incl. errors)
    Evaluate->>Evaluate: error_finding in scope → Gap(unknown, unknown, None)
    Evaluate->>Scaffold: gaps
    Scaffold->>Scaffold: skip gaps with status=unknown
```

<!-- Source: scripts/slz_readiness/discover/az_common.py, evaluate/engine.py, scaffold/engine.py -->

### Baseline vendoring

```mermaid
sequenceDiagram
    autonumber
    participant Dev as Maintainer
    participant Vendor as vendor_baseline.py
    participant ALZ as github.com/Azure/Azure-Landing-Zones-Library
    participant Manifest as _manifest.json
    participant CI as baseline-integrity job

    Dev->>Vendor: --sha <new-sha>
    Vendor->>ALZ: download subtrees (platform/alz, platform/slz)
    Vendor->>Manifest: record every blob's git-sha
    Vendor->>Dev: writes under data/baseline/alz-library/
    Dev->>Dev: update data/baseline/VERSIONS.json
    Dev->>Dev: commit + PR
    CI->>CI: re-hash every vendored blob
    alt manifest mismatch
        CI-->>Dev: fail — tampered vendor tree
    else all match
        CI-->>Dev: green
    end
```

<!-- Source: scripts/slz_readiness/evaluate/vendor_baseline.py, baseline_integrity.py, data/baseline/VERSIONS.json -->

## Why this shape

| Alternative | Rejected because |
|---|---|
| LLM-driven gap analysis | Non-reproducible; cannot be used for compliance evidence |
| Fetch baseline at run time | Offline/air-gapped unusable; supply-chain drift |
| Free-form Bicep generation | AVM compliance impossible to guarantee; what-if behaviour unstable |
| Trust the prompt to stay read-only | One context-saturation bug → writes to production |
| Single monolithic phase | Untestable; each phase is independently golden-testable |

## Extension shape

```mermaid
flowchart LR
    direction TB
    subgraph Closed["Closed-set (modifications require PR + CI)"]
        H["Hooks · pre/post_tool_use.py"]:::c
        M["Matchers · matchers.py MATCHERS dict"]:::c
        T["Templates · ALLOWED_TEMPLATES"]:::c
    end

    subgraph Open["Open (YAML-only 95% of the time)"]
        R["Rules · scripts/evaluate/rules/**/*.yml"]:::o
        RT["Rule→Template · template_registry.py RULE_TO_TEMPLATE"]:::o
    end

    subgraph Dep["Dependent"]
        D["Discoverers · scripts/slz_readiness/discover/*.py"]:::d
    end

    R -.-> M : "kind must exist in MATCHERS"
    RT -.-> T : "value must be in ALLOWED_TEMPLATES"
    D -.-> R : "emits findings rules consume"

    classDef c fill:#1c2128,stroke:#f85149,color:#e6edf3;
    classDef o fill:#1c2128,stroke:#3fb950,color:#e6edf3;
    classDef d fill:#2d333b,stroke:#6d5dfc,color:#e6edf3;
```

## Related reading

- [Plugin Mechanics](/deep-dive/plugin-mechanics) — `apm.yml`, skills, prompts.
- [Hooks](/deep-dive/hooks) — mechanical safety guards.
- [Rule Engine](/deep-dive/evaluate/rule-engine) — deterministic core.
- [Baseline Vendoring](/deep-dive/evaluate/baseline-vendoring) — supply chain.
- [`docs/architecture.md`](https://github.com/msucharda/slz-readiness/blob/main/docs/architecture.md) — first-party architecture note.
- [`docs/anti-hallucination.md`](https://github.com/msucharda/slz-readiness/blob/main/docs/anti-hallucination.md) — safety contract.
