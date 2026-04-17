#!/usr/bin/env bash
# pre-tool-use.sh — block write verbs before any Azure call reaches the wire.
#
# This is the last line of defence: even if the agent is tricked into
# generating an `az group create`, this hook rejects it. The allowlist is
# intentionally narrow; add to it only after security review.
#
# Contract: exit 0 to allow, non-zero to block. The full command line is
# passed on stdin as JSON (Copilot / APM hook convention).
set -euo pipefail

# Slurp the hook payload.
payload=$(cat)

# Extract the command being run. Support two shapes:
#   {"tool":"bash","command":"az group list"}
#   {"tool":"az","args":["group","list"]}
cmd=$(printf '%s' "$payload" | python3 -c '
import json, sys
d = json.load(sys.stdin)
c = d.get("command")
if not c and d.get("args"):
    c = " ".join([d.get("tool",""), *d["args"]])
print((c or "").strip())
' 2>/dev/null || true)

# Nothing to evaluate — allow.
if [[ -z "$cmd" ]]; then
  exit 0
fi

# Only scrutinise Azure-touching commands. Other tools pass through.
if ! [[ "$cmd" =~ (^|[[:space:]])(az|azd|bicep)([[:space:]]|$) ]]; then
  exit 0
fi

# Read-only verb allowlist. Sub-verbs like `az policy assignment list` match too.
# Additions (v0.2.0): summarize, preview, download, effective-permissions, graph.
# See the research report §A-hook-allowlist for rationale — deny-by-default was
# blocking legitimate diagnostic verbs (`az policy state summarize`, etc.).
allow_re='(^|[[:space:]])(list|show|get|query|search|list-.*|show-.*|export|validate|what-if|check|whoami|account|version|summarize|preview|download|effective-permissions|graph)([[:space:]]|$)'
deny_re='(^|[[:space:]])(create|delete|set|update|apply|deploy|start|stop|restart|add|remove|import|upload|grant|revoke|reset|purge|assign|invoke|new|put|patch)([[:space:]]|$)'

if [[ "$cmd" =~ $deny_re ]]; then
  echo "pre-tool-use: BLOCKED write verb in: $cmd" >&2
  echo "slz-readiness is read-only. Scaffold a Bicep change instead." >&2
  exit 1
fi

if [[ "$cmd" =~ $allow_re ]]; then
  exit 0
fi

# Unknown verb — deny by default.
echo "pre-tool-use: BLOCKED unrecognised Azure verb in: $cmd" >&2
echo "Allowed verbs: list, show, get, query, search, export, validate, what-if, check, account, version, summarize, preview, download, effective-permissions, graph." >&2
exit 1
