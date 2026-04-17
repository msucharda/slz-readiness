// archetype-policies.bicep
// Emits every policy assignment declared by a single archetype at a single
// management-group scope. Populated by the scaffold engine from the vendored
// archetype_definition + the referenced policy_assignment JSON files.
//
// NEVER edit `assignments` by hand — the scaffold engine resolves each
// assignment's policyDefinitionId from the pinned baseline. Manual edits will
// drift from the pinned ALZ/SLZ library SHA.

targetScope = 'managementGroup'

@description('Array of policy assignments to create at this MG scope. Each item may carry identityRequired=true for DINE/Modify/Append/DeployIfNotExists effects, in which case a system-assigned identity is attached. The engine also pre-rewrites parameters.effect from Deny -> Audit when rolloutPhase=audit.')
param assignments array

@description('Enforcement mode applied when an assignment does not specify one. Emergency off-switch only — use rolloutPhase for phased rollout.')
@allowed(['Default', 'DoNotEnforce'])
param defaultEnforcementMode string = 'Default'

@description('Informational only — the actual per-assignment effect values were computed by the scaffold engine and are baked into each assignments[*].parameters block. Emitted here so the parameters file is self-describing.')
@allowed(['audit', 'enforce'])
param rolloutPhase string = 'audit'

@description('Deployment location for the system-assigned identity used by DINE/Modify/Append remediation policies. Ignored for assignments where identityRequired is false.')
param identityLocation string = deployment().location

resource assignmentResources 'Microsoft.Authorization/policyAssignments@2024-04-01' = [for a in assignments: {
  name: a.name
  location: (a.?identityRequired ?? false) ? identityLocation : null
  identity: (a.?identityRequired ?? false) ? { type: 'SystemAssigned' } : { type: 'None' }
  properties: {
    displayName: a.displayName
    policyDefinitionId: a.policyDefinitionId
    enforcementMode: a.?enforcementMode ?? defaultEnforcementMode
    parameters: a.?parameters ?? {}
  }
}]
