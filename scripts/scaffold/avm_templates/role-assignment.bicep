// role-assignment.bicep
// Single role assignment on a management-group scope.

targetScope = 'managementGroup'

@description('GUID for the assignment resource name. Use a deterministic guid(...) in the parent template.')
param assignmentName string

@description('Role definition resource id (built-in role id or custom role resource id).')
param roleDefinitionId string

@description('Principal object id (user/group/service principal).')
param principalId string

@description('Principal type hint.')
@allowed(['User', 'Group', 'ServicePrincipal', 'ForeignGroup', 'Device'])
param principalType string = 'Group'

resource assignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: assignmentName
  properties: {
    roleDefinitionId: roleDefinitionId
    principalId: principalId
    principalType: principalType
  }
}
