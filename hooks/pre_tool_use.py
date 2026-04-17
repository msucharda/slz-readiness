#!/usr/bin/env python3
"""pre_tool_use hook — block Azure write verbs before they reach the wire.

Cross-platform replacement for hooks/pre-tool-use.sh. Uses only the Python
stdlib so it runs on Windows without WSL, Git Bash, or MSYS2.

Contract (Copilot / APM hook convention):
* stdin is a JSON payload of shape ``{"command": "...", ...}`` or
  ``{"tool": "...", "args": [...]}``.
* exit 0 = allow. Non-zero = block, with an explanation on stderr.

The allow/deny regex sets are kept byte-identical to the pre-v0.3.0 bash
version so semantics don't drift silently.
"""
from __future__ import annotations

import json
import re
import sys

ALLOW_RE = re.compile(
    r"(^|\s)("
    r"list|show|get|query|search|list-.*|show-.*|export|validate|what-if|"
    r"check|whoami|account|version|summarize|preview|download|"
    r"effective-permissions|graph"
    r")(\s|$)"
)
DENY_RE = re.compile(
    r"(^|\s)("
    r"create|delete|set|update|apply|deploy|start|stop|restart|add|remove|"
    r"import|upload|grant|revoke|reset|purge|assign|invoke|new|put|patch"
    r")(\s|$)"
)
AZURE_TOOL_RE = re.compile(r"(^|\s)(az|azd|bicep)(\s|$)")

ALLOWED_VERBS_MSG = (
    "Allowed verbs: list, show, get, query, search, export, validate, what-if, "
    "check, account, version, summarize, preview, download, "
    "effective-permissions, graph."
)


def extract_command(payload: dict) -> str:
    cmd = payload.get("command")
    if not cmd and payload.get("args"):
        cmd = " ".join([payload.get("tool", ""), *payload["args"]])
    return (cmd or "").strip()


def decide(cmd: str) -> tuple[int, str]:
    if not cmd:
        return 0, ""
    if not AZURE_TOOL_RE.search(cmd):
        return 0, ""
    if DENY_RE.search(cmd):
        return 1, (
            f"pre-tool-use: BLOCKED write verb in: {cmd}\n"
            "slz-readiness is read-only. Scaffold a Bicep change instead."
        )
    if ALLOW_RE.search(cmd):
        return 0, ""
    return 1, (
        f"pre-tool-use: BLOCKED unrecognised Azure verb in: {cmd}\n"
        + ALLOWED_VERBS_MSG
    )


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        # Malformed payload: don't crash the tool call; let it through.
        return 0
    if not isinstance(payload, dict):
        return 0
    rc, msg = decide(extract_command(payload))
    if msg:
        print(msg, file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
