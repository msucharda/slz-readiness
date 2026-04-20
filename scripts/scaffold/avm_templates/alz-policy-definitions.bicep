// alz-policy-definitions
//
// Deploys the ALZ custom `policyDefinitions` and `policySetDefinitions`
// that archetype + sovereignty policy *assignments* reference via
// `/managementGroups/<slz-root>/providers/Microsoft.Authorization/
// policy(Set)Definitions/<name>`.
//
// Without this template, `az deployment ... create` fails with
// `InvalidCreatePolicyAssignmentRequest: The policy definition specified
// ... is out of scope` for every assignment that points at a custom
// ALZ initiative (~29 failures observed in slz-demo run
// 20260420T100848Z).
//
// Raw-resource style (not AVM): no AVM module covers this bulk-emit
// shape, same as `management-groups.bicep`. Definitions deploy first
// (no dependsOn — ARM resolves the outer `for` dependency implicitly
// via the symbolic name reference in `policySetDefinitions[].properties`).
//
// Target scope is the SLZ intermediate-root MG (`slz` alias in
// `mg_alias.json`; canonical default `alz`). The scaffold engine
// rewrites any `/managementGroups/placeholder/` scope tokens in the
// baseline JSON before packing them into params, so
// `policyDefinitions` inside a policySet already reference the
// deploying MG.
targetScope = 'managementGroup'

@description('Custom policyDefinitions to deploy (full ARM shape, minus id/type/etag/systemData).')
param policyDefinitions array

@description('Custom policySetDefinitions to deploy (full ARM shape, minus id/type/etag/systemData). Deployed after policyDefinitions so their inner policyDefinitionId references resolve.')
param policySetDefinitions array

resource defs 'Microsoft.Authorization/policyDefinitions@2023-04-01' = [for def in policyDefinitions: {
  name: def.name
  properties: def.properties
}]

resource sets 'Microsoft.Authorization/policySetDefinitions@2023-04-01' = [for set in policySetDefinitions: {
  name: set.name
  properties: set.properties
  dependsOn: [
    defs
  ]
}]

output policyDefinitionCount int = length(policyDefinitions)
output policySetDefinitionCount int = length(policySetDefinitions)
