// log-analytics.bicep
// Central Log Analytics workspace in the `management` MG subscription, via AVM.
// AVM module version pinned in data/baseline/VERSIONS.json.
//
// Scope: subscription. The target RG is created here (idempotent) so operators
// don't have to pre-provision it — this removes a common deployment-time
// chicken-and-egg error where the RG doesn't exist yet on a fresh subscription.

targetScope = 'subscription'

@description('Workspace name.')
param workspaceName string

@description('Azure region for the resource group and workspace.')
param location string

@description('Name of the resource group that will hold the workspace. Created here if it does not exist.')
param resourceGroupName string = 'rg-slz-management'

@description('Retention in days.')
@minValue(30)
@maxValue(730)
param retentionInDays int = 365

@description('Pricing tier / SKU.')
@allowed(['PerGB2018', 'CapacityReservation'])
param skuName string = 'PerGB2018'

resource managementRg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
}

module workspace 'br/public:avm/res/operational-insights/workspace:0.9.1' = {
  name: 'la-${workspaceName}'
  scope: managementRg
  params: {
    name: workspaceName
    location: location
    dataRetention: retentionInDays
    skuName: skuName
  }
}

output workspaceId string = workspace.outputs.resourceId
output resourceGroupName string = managementRg.name
