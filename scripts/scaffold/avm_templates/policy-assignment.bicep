// policy-assignment.bicep
// Scaffolds a single policy-set assignment on a management-group scope.
// Parameter values come from an archetype_definition in the vendored baseline;
// the scaffold engine resolves the exact policyDefinitionId by cross-referencing
// data/baseline/alz-library/platform/slz/policy_assignments/<name>.alz_policy_assignment.json.
//
// Reviewed via `bicep build` in CI. NEVER deployed by slz-readiness itself.

targetScope = 'managementGroup'

@description('Short assignment name (e.g. Enforce-Sovereign-Global).')
param assignmentName string

@description('Friendly display name.')
param displayName string

@description('policyDefinitionId — a built-in or custom policySetDefinition resource id.')
param policyDefinitionId string

@description('Enforcement mode. Default aligns with the ALZ archetype.')
@allowed(['Default', 'DoNotEnforce'])
param enforcementMode string = 'Default'

@description('Parameter values to pass to the policy set.')
param parameters object = {}

@description('Optional non-compliance messages.')
param nonComplianceMessages array = []

resource assignment 'Microsoft.Authorization/policyAssignments@2024-04-01' = {
  name: assignmentName
  properties: {
    displayName: displayName
    policyDefinitionId: policyDefinitionId
    enforcementMode: enforcementMode
    parameters: parameters
    nonComplianceMessages: nonComplianceMessages
  }
}
