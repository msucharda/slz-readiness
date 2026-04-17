// sovereignty-global-policies.bicep
// Scaffolds the Microsoft Cloud for Sovereignty Global Policies initiative
// (Enforce-Sovereign-Global). Deploy at the SLZ root management group.
//
// policySetDefinition id is a pinned literal from the vendored baseline:
//   data/baseline/alz-library/platform/slz/policy_assignments/
//     Enforce-Sovereign-Global.alz_policy_assignment.json
// Source sha: see data/baseline/VERSIONS.json.
//
// NOTE (v0.2.0): split out from the old sovereignty-policies.bicep so that
// each of the three sovereignty assignments can be deployed at its correct
// management group. Do NOT combine Global and Confidential back into one
// file — they target different MGs.

targetScope = 'managementGroup'

@description('Enforcement mode applied to the Global policy set. Emergency off-switch only — use rolloutPhase for phased rollout.')
@allowed(['Default', 'DoNotEnforce'])
param enforcementMode string = 'Default'

@description('Rollout phase for the Deny-class effects. "audit" = log non-compliance without blocking (Wave 1). "enforce" = actively Deny non-compliant writes (Wave 2). Default is audit; operators must opt into enforce after observing compliance data.')
@allowed(['audit', 'enforce'])
param rolloutPhase string = 'audit'

@description('List of allowed Azure locations. Populated by scaffold from the SLZ baseline defaults; can be overridden.')
param listOfAllowedLocations array

var globalPolicySetId = '/providers/Microsoft.Authorization/policySetDefinitions/c1cbff38-87c0-4b9f-9f70-035c7a3b5523'
var effectValue = rolloutPhase == 'enforce' ? 'Deny' : 'Audit'

resource globalAssignment 'Microsoft.Authorization/policyAssignments@2024-04-01' = {
  name: 'Enforce-Sovereign-Global'
  properties: {
    displayName: 'Sovereignty Baseline - Global Policies'
    policyDefinitionId: globalPolicySetId
    definitionVersion: '1.*.*'
    enforcementMode: enforcementMode
    parameters: {
      effect: { value: effectValue }
      listOfAllowedLocations: { value: listOfAllowedLocations }
    }
  }
}
