// archetype-policies.bicep
// Emits every policy assignment declared by a single archetype at a single
// management-group scope. Populated by the scaffold engine from the vendored
// archetype_definition + the referenced policy_assignment JSON files.
//
// NEVER edit `assignments` by hand — the scaffold engine resolves each
// assignment's policyDefinitionId from the pinned baseline. Manual edits will
// drift from the pinned ALZ/SLZ library SHA.

targetScope = 'managementGroup'

@description('Array of policy assignments to create at this MG scope.')
param assignments array

@description('Enforcement mode applied when an assignment does not specify one.')
@allowed(['Default', 'DoNotEnforce'])
param defaultEnforcementMode string = 'Default'

resource assignmentResources 'Microsoft.Authorization/policyAssignments@2024-04-01' = [for a in assignments: {
  name: a.name
  properties: {
    displayName: a.displayName
    policyDefinitionId: a.policyDefinitionId
    enforcementMode: contains(a, 'enforcementMode') ? a.enforcementMode : defaultEnforcementMode
    parameters: contains(a, 'parameters') ? a.parameters : {}
  }
}]
