#!/usr/bin/env bash
# Back-compat shim. Canonical hook is hooks/post_tool_use.py.
exec python3 "$(dirname "$0")/post_tool_use.py" "$@"