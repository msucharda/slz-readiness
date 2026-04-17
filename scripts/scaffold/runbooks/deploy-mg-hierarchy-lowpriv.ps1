<#
.SYNOPSIS
    Deploy the SLZ management-group hierarchy as an operator WITHOUT tenant-root
    (`/`) RBAC, using direct ARM REST PUTs.

.DESCRIPTION
    ARM tenant-scope deployments (`az deployment tenant create`) require
    `Microsoft.Resources/deployments/whatIf/action` + `.../write` at scope `/`.
    Many enterprise principals (notably MCAPS / Microsoft-internal accounts)
    hold `Owner` only at the tenant-root **management group**, not at `/`, so
    `whatIf`/`create` fails with `AuthorizationFailed` even though the
    downstream `Microsoft.Management/managementGroups/write` calls would
    succeed.

    This runbook side-steps that by issuing `PUT
    /providers/Microsoft.Management/managementGroups/{name}?api-version=2023-04-01`
    for each MG in parent-first order. Each PUT only requires
    `Microsoft.Management/managementGroups/write` at the **parent MG** —
    granted by `Management Group Contributor` or `Owner` at MG scope.

    Equivalent to deploying `management-groups.bicep`, but skips ARM deployment
    metadata entirely. Use this only when the `az deployment tenant` path is
    blocked by RBAC.

.PARAMETER TenantId
    The Entra tenant id. Used for `az login --tenant`.

.PARAMETER ParentManagementGroupId
    Parent MG id for the top-level `slz` MG. Typically the tenant-root MG id
    (which equals the tenant id).

.PARAMETER SlzDisplayName
    Display name for the top-level `slz` MG. Defaults to "Sovereign Landing Zone".

.PARAMETER WhatIf
    If set, prints each PUT URL without executing it.

.EXAMPLE
    ./deploy-mg-hierarchy-lowpriv.ps1 `
        -TenantId 00000000-0000-0000-0000-000000000000 `
        -ParentManagementGroupId 00000000-0000-0000-0000-000000000000

.NOTES
    Emitted by slz-readiness. Review before running.
    The slz-readiness plugin never executes this file; HITL deployment is the
    contract (see how-to-deploy.md).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $TenantId,
    [Parameter(Mandatory = $true)] [string] $ParentManagementGroupId,
    [string] $SlzDisplayName = 'Sovereign Landing Zone',
    [switch] $WhatIf
)

$ErrorActionPreference = 'Stop'

# MG hierarchy — mirrors management-groups.bicep exactly. Parent-first order
# so each PUT's `details.parent.id` already exists.
$mgs = @(
    @{ name = 'slz';                 parent = $ParentManagementGroupId; displayName = $SlzDisplayName },
    @{ name = 'platform';            parent = 'slz';           displayName = 'Platform' },
    @{ name = 'landingzones';        parent = 'slz';           displayName = 'Landing zones' },
    @{ name = 'sandbox';             parent = 'slz';           displayName = 'Sandbox' },
    @{ name = 'decommissioned';      parent = 'slz';           displayName = 'Decommissioned' },
    @{ name = 'management';          parent = 'platform';      displayName = 'Management' },
    @{ name = 'connectivity';        parent = 'platform';      displayName = 'Connectivity' },
    @{ name = 'identity';            parent = 'platform';      displayName = 'Identity' },
    @{ name = 'security';            parent = 'platform';      displayName = 'Security' },
    @{ name = 'corp';                parent = 'landingzones';  displayName = 'Corp' },
    @{ name = 'online';              parent = 'landingzones';  displayName = 'Online' },
    @{ name = 'public';              parent = 'landingzones';  displayName = 'Public' },
    @{ name = 'confidential_corp';   parent = 'landingzones';  displayName = 'Confidential Corp' },
    @{ name = 'confidential_online'; parent = 'landingzones';  displayName = 'Confidential Online' }
)

Write-Host "Acquiring ARM bearer token for tenant $TenantId ..." -ForegroundColor Cyan
$token = (az account get-access-token --tenant $TenantId --resource https://management.azure.com/ --query accessToken -o tsv)
if (-not $token) {
    throw "Failed to acquire access token. Run 'az login --tenant $TenantId' first."
}
$headers = @{
    Authorization  = "Bearer $token"
    'Content-Type' = 'application/json'
}

foreach ($mg in $mgs) {
    $uri = "https://management.azure.com/providers/Microsoft.Management/managementGroups/$($mg.name)?api-version=2023-04-01"
    $body = @{
        properties = @{
            displayName = $mg.displayName
            details = @{
                parent = @{
                    id = "/providers/Microsoft.Management/managementGroups/$($mg.parent)"
                }
            }
        }
    } | ConvertTo-Json -Depth 5 -Compress

    Write-Host "PUT $($mg.name) (parent=$($mg.parent))" -ForegroundColor Yellow
    if ($WhatIf) {
        Write-Host "  [WhatIf] would PUT $uri" -ForegroundColor DarkGray
        Write-Host "  [WhatIf] body: $body"     -ForegroundColor DarkGray
        continue
    }

    try {
        $resp = Invoke-RestMethod -Method PUT -Uri $uri -Headers $headers -Body $body
        Write-Host "  OK: $($resp.id)" -ForegroundColor Green
    }
    catch {
        $status = $_.Exception.Response.StatusCode.value__
        Write-Host "  FAIL ($status): $($_.Exception.Message)" -ForegroundColor Red
        throw
    }
}

Write-Host "`nDone. Verify with:" -ForegroundColor Cyan
Write-Host "  az account management-group list --query `"[].name`" -o tsv"
