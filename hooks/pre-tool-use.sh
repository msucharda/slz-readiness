#!/usr/bin/env bash
# Back-compat shim. The canonical hook is hooks/pre_tool_use.py; this wrapper
# exists so callers that still reference the .sh path continue to work.
exec python3 "$(dirname "$0")/pre_tool_use.py" "$@"