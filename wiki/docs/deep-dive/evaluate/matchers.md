# Matchers

## At a glance

| Matcher kind | Purpose | Example rule |
|---|---|---|
| `equals` | Exact value match on one field | `mg.slz.hierarchy_shape` |
| `contains_all` | Observed set ⊇ required set | Role-assignment lists |
| `policy_assignments_include` | A specific policySetDefinitionId is present in any assignment | `sovereignty.slz.global_policies` |
| `archetype_policies_applied` | All expected policies for a named archetype are applied | `archetype.*.policies` |
| `any_subscription_has_workspace` | At least one subscription in scope has a Log Analytics workspace | `logging.slz.workspace_exists` |

Source: [`matchers.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/evaluate/matchers.py), registry at [`matchers.py:98`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/evaluate/matchers.py#L98).

## Signature

Every matcher conforms to one type:

```python
Matcher = Callable[[MatcherSpec, list[Finding]], tuple[bool, dict]]
```

- `spec` — the `matcher:` block from the rule YAML (arbitrary dict with a `kind` discriminator).
- `findings` — the in-scope subset.
- Return `(ok, observed)`:
  - `ok = True` — rule passes, `observed` is informational.
  - `ok = False` — rule fails, `observed` is the evidence written into the Gap.

## The MATCHERS registry

[`matchers.py:98`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/evaluate/matchers.py#L98):

```python
MATCHERS: dict[str, Matcher] = {
    "equals": _equals,
    "contains_all": _contains_all,
    "policy_assignments_include": _policy_assignments_include,
    "archetype_policies_applied": _archetype_policies_applied,
    "any_subscription_has_workspace": _any_subscription_has_workspace,
}
```

Adding a new matcher kind requires:

1. A new `_new_matcher(spec, findings)` function in `matchers.py`.
2. Registration in the `MATCHERS` dict.
3. A parametrized unit test.
4. A rule YAML that uses it with golden fixtures updated.

This is the closed-set safety surface — rules can't smuggle in arbitrary Python.

## Dispatch

```mermaid
flowchart LR
    Rule["Rule<br>matcher.kind = X"]:::r
    Engine["engine.evaluate_rule"]:::e
    Reg["MATCHERS dict"]:::r
    Fn["matcher function"]:::m
    Ok["(True, observed)"]:::ok
    Bad["(False, observed)"]:::bad

    Rule --> Engine
    Engine -- "lookup kind" --> Reg
    Reg --> Fn
    Engine -- "(spec, findings)" --> Fn
    Fn --> Ok
    Fn --> Bad
    Ok --> Engine
    Bad --> Engine

    classDef r fill:#161b22,stroke:#30363d,color:#e6edf3;
    classDef e fill:#2d333b,stroke:#6d5dfc,color:#e6edf3;
    classDef m fill:#2d333b,stroke:#3fb950,color:#e6edf3;
    classDef ok fill:#1c2128,stroke:#3fb950,color:#e6edf3;
    classDef bad fill:#1c2128,stroke:#f85149,color:#e6edf3;
```

## Matcher-by-matcher

### `equals`

```yaml
matcher:
  kind: equals
  field: data.display_name
  value: "SLZ Platform"
```

Passes when a single finding's `field` equals `value`. `observed` reports the mismatched value. Used sparingly — most shape questions are set-based.

### `contains_all`

```yaml
matcher:
  kind: contains_all
  field: data.required_role_definition_ids
  values: ["acdd72a7-...", "b24988ac-..."]
```

Passes when `findings[].field` (treated as a set) is a superset of `values`. Missing elements go into `observed.missing`.

### `policy_assignments_include`

```yaml
matcher:
  kind: policy_assignments_include
  policy_set_definition_id: c1cbff38-87c0-4b9f-9f70-035c7a3b5523
```

Walks `policy_assignment` findings in scope and passes if **any** has matching `policySetDefinitionId`. `observed` lists the scope searched and the assignment count.

### `archetype_policies_applied`

```yaml
matcher:
  kind: archetype_policies_applied
  archetype: alz_corp
```

Cross-references:

- `management_group` findings whose name/tag marks it as the given archetype.
- `policy_assignment` findings at that MG's scope.
- The baseline policy list for that archetype (loaded from the pinned ALZ Library).

Passes when the in-scope MGs all carry the expected policy assignment set. `observed` names the MG and missing policy ids.

### `any_subscription_has_workspace`

```yaml
matcher:
  kind: any_subscription_has_workspace
  min_retention_days: 30
```

Aggregate-style: passes when **any** `log_analytics_workspace` finding in scope has `data.retention_in_days >= 30`. `observed` lists the workspaces inspected and their retention values.

## Why only five

```mermaid
flowchart LR
    Goal["Goal: rules authored in YAML<br>by non-Python SMEs"]:::g

    subgraph WhatMatters["Shape questions actually asked"]
        direction TB
        Q1["Exact value?"]:::q
        Q2["Required set present?"]:::q
        Q3["Specific policy applied?"]:::q
        Q4["Archetype's full policy bundle applied?"]:::q
        Q5["At least one workspace present?"]:::q
    end

    subgraph Matchers["Five matchers"]
        direction TB
        M1["equals"]:::m
        M2["contains_all"]:::m
        M3["policy_assignments_include"]:::m
        M4["archetype_policies_applied"]:::m
        M5["any_subscription_has_workspace"]:::m
    end

    Q1 --> M1
    Q2 --> M2
    Q3 --> M3
    Q4 --> M4
    Q5 --> M5

    Goal -.-> WhatMatters

    classDef g fill:#2d333b,stroke:#6d5dfc,color:#e6edf3;
    classDef q fill:#161b22,stroke:#30363d,color:#e6edf3;
    classDef m fill:#1c2128,stroke:#3fb950,color:#e6edf3;
```

The five matchers cover every question the 14 rules ask. Adding an unconstrained "custom JSON path + operator" matcher would explode the surface area for hallucination and would force rule authors to learn JSONPath semantics. The five-kind ceiling is a product choice.

## Observed payload conventions

Each matcher returns `observed` with a small, documented shape so the Plan phase can rely on it and Scaffold can populate template parameters from it:

| Matcher | `observed` keys |
|---|---|
| `equals` | `expected`, `actual`, `field` |
| `contains_all` | `required`, `present`, `missing` |
| `policy_assignments_include` | `scope`, `expected_id`, `assignments_seen` |
| `archetype_policies_applied` | `archetype`, `mg_id`, `missing_policy_ids` |
| `any_subscription_has_workspace` | `subscriptions_scanned`, `workspaces`, `threshold` |

The Plan prompt reads these shapes when composing remediation bullets.

## Related reading

- [Rule Engine](/deep-dive/evaluate/rule-engine) — how matchers are dispatched.
- [Rules Catalog](/deep-dive/evaluate/rules-catalog) — which rule uses which matcher.
- [Baseline Vendoring](/deep-dive/evaluate/baseline-vendoring) — where `archetype_policies_applied` gets its expected policy list.
