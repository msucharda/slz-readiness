// sovereignty-confidential-policies.bicep
// Scaffolds the Microsoft Cloud for Sovereignty Confidential Policies
// initiative (Enforce-Sovereign-Conf). Deploy at confidential_corp and
// confidential_online management groups (separate deployments — targetScope
// is managementGroup so one deployment affects one MG).
//
// policySetDefinition id is a pinned literal from the vendored baseline:
//   data/baseline/alz-library/platform/slz/policy_assignments/
//     Enforce-Sovereign-Conf.alz_policy_assignment.json
// Source sha: see data/baseline/VERSIONS.json.

targetScope = 'managementGroup'

@description('Enforcement mode applied to the Confidential policy set. Emergency off-switch only — use rolloutPhase for phased rollout.')
@allowed(['Default', 'DoNotEnforce'])
param enforcementMode string = 'Default'

@description('Rollout phase for the Deny-class effects. "audit" = log non-compliance without blocking (Wave 1). "enforce" = actively Deny non-compliant writes (Wave 2). Default is audit.')
@allowed(['audit', 'enforce'])
param rolloutPhase string = 'audit'

var confidentialPolicySetId = '/providers/Microsoft.Authorization/policySetDefinitions/03de05a4-c324-4ccd-882f-a814ea8ab9ea'
var effectValue = rolloutPhase == 'enforce' ? 'Deny' : 'Audit'

resource confidentialAssignment 'Microsoft.Authorization/policyAssignments@2024-04-01' = {
  name: 'Enforce-Sovereign-Conf'
  properties: {
    displayName: 'Sovereignty Baseline - Confidential Policies'
    policyDefinitionId: confidentialPolicySetId
    definitionVersion: '1.*.*'
    enforcementMode: enforcementMode
    parameters: {
      effect: { value: effectValue }
    }
  }
}
