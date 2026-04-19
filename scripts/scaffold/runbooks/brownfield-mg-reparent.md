# Brownfield MG re-parent runbook

Operator-run companion to `how-to-deploy.md` emitted by `slz-scaffold`.
Covers the specific failure mode behind blocker #1 of run
`20260419T132307Z`: `ParentManagementGroupCannotBeChanged`.

## When to read this

Your run's `scaffold.warnings` contains:

- `[brownfield] mg_alias.json detected` — canonical SLZ roles have been
  mapped onto existing MGs in your tenant, AND
- `[brownfield] rewrite_names auto-enabled` (or you passed
  `--rewrite-names` yourself) — the emitted Bicep will use your tenant's
  MG names, AND
- `management-groups.bicep` was emitted (one or more MGs must be
  created).

If all three are true, you are re-parenting part of an existing MG tree.
Bicep **cannot** do this for you — the `parent` property of
`Microsoft.Management/managementGroups` is **immutable**. A naïve
`az deployment mg create` will fail with
`ParentManagementGroupCannotBeChanged`, or worse, silently no-op because
the names already exist in an unrelated branch.

## Why the Bicep alone is not enough

The scaffold engine emits conditional `create<Name>` flags — set to
`false` for every MG it found already on the tenant (via its aliased
name). This means the deployment will **skip** already-present MGs
rather than attempting to re-create them. That prevents the name
collision, but it does **not** move an existing MG to sit under the
canonical SLZ parent the policies expect.

Example:

- `mg_alias.json`: `{ "slz": "alz", "landingzones": "workloads" }`
- Tenant's current tree: `tenant-root → sucharda → alz → platform → workloads`
- Canonical SLZ tree the policies target:
  `tenant-root → slz → {platform, landingzones, sandbox, decommissioned}`

With `rewrite_names` on, the emitted Bicep wires `workloads` under
`alz`, matching the tenant. Good. But if the operator wants policies
scoped at `alz` to cascade to `workloads` the way canonical
`landingzones` would, `workloads` must in fact be a child of `alz` in
the tenant. If the tenant's `workloads` currently sits elsewhere (say
under `sucharda` directly), the deployment will fail or misbind.

## Procedure

### 1. List current parentage

For each alias entry:

```bash
az account management-group show \
  --name "<aliased-mg-id>" \
  --expand \
  --query 'properties.details.parent.name'
```

Record the real parent of every MG mentioned in `mg_alias.json`.

### 2. Compare against the canonical tree

The canonical SLZ parent-of relationships the scaffold engine expects:

| Canonical MG       | Canonical parent           |
|--------------------|----------------------------|
| `slz`              | tenant root                |
| `platform`         | `slz`                      |
| `landingzones`     | `slz`                      |
| `sandbox`          | `slz`                      |
| `decommissioned`   | `slz`                      |
| `management`       | `platform`                 |
| `connectivity`     | `platform`                 |
| `identity`         | `platform`                 |
| `security`         | `platform`                 |
| `corp`             | `landingzones`             |
| `online`           | `landingzones`             |
| `public`           | `landingzones`             |
| `confidential_corp`    | `landingzones`         |
| `confidential_online`  | `landingzones`         |

Translate each canonical parent through `mg_alias.json` to find the
**expected** real parent. Any row where the observed parent
differs from the expected parent is a move you must perform
**before** deploying.

### 3. Move MGs that are in the wrong place

For every mismatched row:

```bash
az account management-group move \
  --group-id   "<aliased-mg-id>" \
  --parent-id  "<aliased-parent-mg-id>"
```

> Note: This **does** cascade policy/role assignments with the MG —
> that is the point. Review `az deployment mg what-if` output after
> each move to confirm the blast radius.

### 4. Verify before deploy

```bash
az account management-group show \
  --name "<aliased-mg-id>" \
  --expand \
  --query 'properties.details.parent.name'
```

Repeat until every aliased MG's real parent equals the aliased
canonical parent from the table above. **Only then** run the
`az deployment mg create` commands from `how-to-deploy.md`.

## What the agent will never do

`az account management-group move`, `create`, `delete`, and any
`az deployment … create` are blocked by `hooks/pre_tool_use.py`. This
runbook exists because the agent physically cannot perform these
re-parent operations on your behalf — HITL is the contract.
