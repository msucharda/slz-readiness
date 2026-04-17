"""Discover scripts — read-only Azure queries → findings.json.

All queries use the `az` CLI (user must be logged in). Every command is
recorded in the finding's `query_cmd` field for reproducibility. No write
verbs. No Azure mutation.

When a command fails, callers should use ``AzError`` (not silently swallow
the exception) so the evaluator can surface an ``unknown``-severity gap rather
than reporting the scope as compliant.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from .. import _trace

# Resolve `az` via PATHEXT-aware lookup so Windows finds `az.cmd`.
# subprocess.run with argv list and shell=False does not honour PATHEXT on
# Windows; shutil.which does. Fall back to the bare name for environments
# that inject az only into the subprocess PATH.
_AZ = shutil.which("az") or "az"


class AzError(RuntimeError):
    """Raised by run_az when the CLI returns non-zero.

    ``kind`` classifies the failure for the unknown-severity pipeline:

    * ``permission_denied`` — 403 / Authorization / AuthorizationFailed
    * ``not_found``         — 404 / ResourceNotFound / MG missing
    * ``rate_limited``      — 429 / TooManyRequests / RateLimit
    * ``network``           — any other non-zero with no stderr classification
    """

    def __init__(self, kind: str, cmd: list[str], stderr: str) -> None:
        super().__init__(f"[{kind}] az {' '.join(cmd)}: {stderr.strip()[:400]}")
        self.kind = kind
        self.cmd = cmd
        self.stderr = stderr


def _classify(stderr: str, returncode: int) -> str:
    s = stderr.lower()
    if "forbidden" in s or "authorizationfailed" in s or "does not have authorization" in s:
        return "permission_denied"
    if "notfound" in s or "not found" in s or "was not found" in s:
        return "not_found"
    if "throttl" in s or "toomanyrequests" in s or "rate limit" in s:
        return "rate_limited"
    return "network"


def run_az(args: list[str]) -> Any:
    """Run `az <args>` and return parsed JSON output.

    Raises ``AzError`` with a classified ``kind`` on non-zero exit. Writes a
    trace line for both success and failure if a tracer is active.
    """
    cmd = [_AZ, *args, "-o", "json"]
    res = subprocess.run(cmd, check=False, capture_output=True, text=True)  # noqa: S603
    if res.returncode != 0:
        kind = _classify(res.stderr, res.returncode)
        _trace.log(
            "az.cmd",
            cmd=cmd,
            returncode=res.returncode,
            kind=kind,
            stderr_len=len(res.stderr or ""),
        )
        raise AzError(kind, args, res.stderr)
    _trace.log("az.cmd", cmd=cmd, returncode=0, stdout_len=len(res.stdout or ""))
    return json.loads(res.stdout) if res.stdout.strip() else []


def az_cmd_str(args: list[str]) -> str:
    return "az " + " ".join(args) + " -o json"


def error_finding(resource_type: str, resource_id: str, scope: str, args: list[str], err: AzError) -> dict[str, Any]:
    """Build a finding that captures a discovery failure.

    Evaluate turns these into ``status: unknown, severity: unknown`` gaps so
    the plan phase can flag them under "Blocked discoveries".
    """
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "scope": scope,
        "observed_state": {
            "error": err.kind,
            "message": err.stderr.strip()[:400],
        },
        "query_cmd": az_cmd_str(args),
    }
