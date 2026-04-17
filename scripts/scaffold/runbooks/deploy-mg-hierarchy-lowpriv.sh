#!/usr/bin/env bash
# deploy-mg-hierarchy-lowpriv.sh
#
# Bash equivalent of deploy-mg-hierarchy-lowpriv.ps1. See the PowerShell
# header for the full rationale.
#
# Short version: ARM tenant-scope deployment (`az deployment tenant create`)
# requires `Microsoft.Resources/deployments/whatIf/action` + `.../write` at
# scope `/`. MCAPS / enterprise principals often hold `Owner` only at the
# tenant-root MG, not at `/`. This script PUTs each MG resource directly at
# MG scope using the management-group contributor permission the operator
# already has.
#
# Emitted by slz-readiness. Review before running. The plugin itself never
# executes this file — HITL deployment is the contract (see how-to-deploy.md).
#
# Usage:
#   ./deploy-mg-hierarchy-lowpriv.sh \
#       --tenant-id <tenant-id> \
#       --parent-mg-id <parent-mg-id> \
#       [--slz-display-name "Sovereign Landing Zone"] \
#       [--whatif]

set -euo pipefail

TENANT_ID=""
PARENT_MG_ID=""
SLZ_DISPLAY_NAME="Sovereign Landing Zone"
WHATIF="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tenant-id)         TENANT_ID="$2";        shift 2 ;;
        --parent-mg-id)      PARENT_MG_ID="$2";     shift 2 ;;
        --slz-display-name)  SLZ_DISPLAY_NAME="$2"; shift 2 ;;
        --whatif)            WHATIF="true";         shift 1 ;;
        -h|--help)
            sed -n '1,25p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown arg: $1" >&2
            exit 2
            ;;
    esac
done

if [[ -z "$TENANT_ID" || -z "$PARENT_MG_ID" ]]; then
    echo "ERROR: --tenant-id and --parent-mg-id are both required" >&2
    exit 2
fi

# MG hierarchy — mirrors management-groups.bicep exactly. Parent-first order
# so each PUT's `details.parent.id` already exists.
# Format: "name|parent|displayName"
MGS=(
    "slz|${PARENT_MG_ID}|${SLZ_DISPLAY_NAME}"
    "platform|slz|Platform"
    "landingzones|slz|Landing zones"
    "sandbox|slz|Sandbox"
    "decommissioned|slz|Decommissioned"
    "management|platform|Management"
    "connectivity|platform|Connectivity"
    "identity|platform|Identity"
    "security|platform|Security"
    "corp|landingzones|Corp"
    "online|landingzones|Online"
    "public|landingzones|Public"
    "confidential_corp|landingzones|Confidential Corp"
    "confidential_online|landingzones|Confidential Online"
)

echo "Acquiring ARM bearer token for tenant ${TENANT_ID} ..."
TOKEN="$(az account get-access-token --tenant "${TENANT_ID}" --resource https://management.azure.com/ --query accessToken -o tsv)"
if [[ -z "${TOKEN}" ]]; then
    echo "ERROR: could not acquire access token — run 'az login --tenant ${TENANT_ID}' first" >&2
    exit 1
fi

for entry in "${MGS[@]}"; do
    IFS='|' read -r NAME PARENT DISPLAY <<<"${entry}"
    URI="https://management.azure.com/providers/Microsoft.Management/managementGroups/${NAME}?api-version=2023-04-01"
    BODY=$(cat <<EOF
{"properties":{"displayName":"${DISPLAY}","details":{"parent":{"id":"/providers/Microsoft.Management/managementGroups/${PARENT}"}}}}
EOF
)

    echo "PUT ${NAME} (parent=${PARENT})"
    if [[ "${WHATIF}" == "true" ]]; then
        echo "  [WhatIf] would PUT ${URI}"
        echo "  [WhatIf] body: ${BODY}"
        continue
    fi

    HTTP_CODE=$(curl -sS -o /tmp/slz-mg-resp.json -w "%{http_code}" \
        -X PUT "${URI}" \
        -H "Authorization: Bearer ${TOKEN}" \
        -H "Content-Type: application/json" \
        -d "${BODY}")

    if [[ "${HTTP_CODE}" == "200" || "${HTTP_CODE}" == "201" ]]; then
        echo "  OK (HTTP ${HTTP_CODE})"
    else
        echo "  FAIL (HTTP ${HTTP_CODE}):"
        cat /tmp/slz-mg-resp.json >&2
        echo >&2
        exit 1
    fi
done

echo
echo "Done. Verify with:"
echo "  az account management-group list --query '[].name' -o tsv"
