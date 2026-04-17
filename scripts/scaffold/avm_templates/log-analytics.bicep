// log-analytics.bicep
// Central Log Analytics workspace in the `management` MG subscription, via AVM.
// AVM module version pinned in data/baseline/VERSIONS.json.

targetScope = 'resourceGroup'

@description('Workspace name.')
param workspaceName string

@description('Azure region.')
param location string = resourceGroup().location

@description('Retention in days.')
@minValue(30)
@maxValue(730)
param retentionInDays int = 365

@description('Pricing tier / SKU.')
@allowed(['PerGB2018', 'CapacityReservation'])
param skuName string = 'PerGB2018'

module workspace 'br/public:avm/res/operational-insights/workspace:0.9.1' = {
  name: 'la-${workspaceName}'
  params: {
    name: workspaceName
    location: location
    dataRetention: retentionInDays
    skuName: skuName
  }
}

output workspaceId string = workspace.outputs.resourceId
