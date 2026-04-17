// sovereignty-policies.bicep
// Scaffolds the Microsoft Cloud for Sovereignty policy-set assignments required
// by the confidential_corp / confidential_online archetypes.
//
// policySetDefinition ids are pinned literals copied verbatim from the vendored
// baseline's policy_assignments/Enforce-Sovereign-Conf.alz_policy_assignment.json
// and Enforce-Sovereign-Global.alz_policy_assignment.json.
// Source sha: see data/baseline/VERSIONS.json.

targetScope = 'managementGroup'

@description('Deploy the Confidential policies set (Enforce-Sovereign-Conf).')
param deployConfidential bool = true

@description('Deploy the Global policies set (Enforce-Sovereign-Global).')
param deployGlobal bool = true

@description('Enforcement mode applied to both sets.')
@allowed(['Default', 'DoNotEnforce'])
param enforcementMode string = 'Default'

@description('List of allowed locations for the Global policy set. Empty array means "use defaults from the baseline".')
param listOfAllowedLocations array = []

// policySetDefinition ids taken from the vendored assignment JSONs. Do NOT change by hand;
// run vendor_baseline and update data/baseline/VERSIONS.json instead.
var confidentialPolicySetId = '/providers/Microsoft.Authorization/policySetDefinitions/03de05a4-c324-4ccd-882f-a814ea8ab9ea'
var globalPolicySetId = '/providers/Microsoft.Authorization/policySetDefinitions/c1cbff38-87c0-4b9f-9f70-035c7a3b5523'

resource confidential 'Microsoft.Authorization/policyAssignments@2024-04-01' = if (deployConfidential) {
  name: 'Enforce-Sovereign-Conf'
  properties: {
    displayName: 'Sovereignty Baseline - Confidential Policies'
    policyDefinitionId: confidentialPolicySetId
    definitionVersion: '1.*.*'
    enforcementMode: enforcementMode
  }
}

resource global 'Microsoft.Authorization/policyAssignments@2024-04-01' = if (deployGlobal) {
  name: 'Enforce-Sovereign-Global'
  properties: {
    displayName: 'Sovereignty Baseline - Global Policies'
    policyDefinitionId: globalPolicySetId
    definitionVersion: '1.*.*'
    enforcementMode: enforcementMode
    parameters: {
      effect: { value: 'Deny' }
      listOfAllowedLocations: { value: listOfAllowedLocations }
    }
  }
}
