"""Discover scripts — read-only Azure queries → findings.json.

All queries use the `az` CLI (user must be logged in). Every command is
recorded in the finding's `query_cmd` field for reproducibility. No write
verbs. No Azure mutation.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any


def run_az(args: list[str]) -> Any:
    """Run `az <args>` and return parsed JSON output."""
    cmd = ["az", *args, "-o", "json"]
    res = subprocess.run(cmd, check=True, capture_output=True, text=True)  # noqa: S603
    return json.loads(res.stdout) if res.stdout.strip() else []


def az_cmd_str(args: list[str]) -> str:
    return "az " + " ".join(args) + " -o json"
