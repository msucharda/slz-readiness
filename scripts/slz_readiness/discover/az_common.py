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
import os
import shutil
import signal
import subprocess
import sys
from typing import Any

from .. import _trace

# Per-call subprocess timeout (seconds). Without this, a stalled ARM/Graph
# call hangs the whole discovery pipeline indefinitely.
_DEFAULT_TIMEOUT = 60.0

_IS_WINDOWS = sys.platform.startswith("win")


def _timeout() -> float:
    raw = os.environ.get("SLZ_AZ_TIMEOUT")
    if not raw:
        return _DEFAULT_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill ``proc`` and all its descendants.

    Needed because ``az`` on Windows is a ``.cmd`` wrapper that spawns
    ``python.exe`` as a grandchild. ``Popen.kill()`` only terminates the direct
    child (cmd.exe), leaving the grandchild holding the stdout/stderr pipes —
    which makes any subsequent ``communicate()`` hang indefinitely. On POSIX
    we created a new process group so ``killpg`` reaches the descendants.
    """
    if proc.poll() is not None:
        return
    if _IS_WINDOWS:
        subprocess.run(  # noqa: S603,S607
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # type: ignore[attr-defined]  # POSIX only
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

# Resolve `az` via PATHEXT-aware lookup so Windows finds `az.cmd`.
# subprocess.run with argv list and shell=False does not honour PATHEXT on
# Windows; shutil.which does. Fall back to the bare name for environments
# that inject az only into the subprocess PATH.
_AZ = shutil.which("az") or "az"


class AzError(RuntimeError):
    """Raised by run_az when the CLI returns non-zero.

    ``kind`` classifies the failure for the unknown-severity pipeline:

    * ``permission_denied``  — 403 / Authorization / AuthorizationFailed
    * ``not_found``          — 404 / ResourceNotFound / MG missing
    * ``rate_limited``       — 429 / TooManyRequests / RateLimit
    * ``missing_extension``  — required az CLI extension not installed
    * ``network``            — any other non-zero with no stderr classification
    """

    def __init__(self, kind: str, cmd: list[str], stderr: str) -> None:
        super().__init__(f"[{kind}] az {' '.join(cmd)}: {stderr.strip()[:400]}")
        self.kind = kind
        self.cmd = cmd
        self.stderr = stderr


def _classify(stderr: str, returncode: int) -> str:
    s = stderr.lower()
    # Must match before ``not_found`` because the extension-missing message from
    # the az CLI ("The command requires the extension ...") also contains the
    # substring "command group ... is not" on some locales.
    if (
        "requires the extension" in s
        or "is misspelled or not recognized" in s
        or "no tty available" in s and "extension" in s
    ):
        return "missing_extension"
    if "forbidden" in s or "authorizationfailed" in s or "does not have authorization" in s:
        return "permission_denied"
    if "notfound" in s or "not found" in s or "was not found" in s:
        return "not_found"
    if "throttl" in s or "toomanyrequests" in s or "rate limit" in s:
        return "rate_limited"
    return "network"


def run_az(args: list[str]) -> Any:
    """Run `az <args>` and return parsed JSON output.

    Raises ``AzError`` with a classified ``kind`` on non-zero exit or on
    timeout. Writes a trace line for both success and failure if a tracer is
    active.
    """
    cmd = [_AZ, *args, "-o", "json"]
    timeout = _timeout()
    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if _IS_WINDOWS:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)  # noqa: S603
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            # Pipes still held by a leaked grandchild — abandon them.
            try:
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()
            except Exception:  # noqa: BLE001
                pass
            stdout, stderr = "", ""
        msg = f"az call exceeded {timeout:.0f}s timeout: {' '.join(args)}"
        _trace.log(
            "az.cmd",
            cmd=cmd,
            returncode=None,
            kind="network",
            timeout_seconds=timeout,
            stderr_len=len(msg),
        )
        raise AzError("network", args, msg) from None

    if proc.returncode != 0:
        kind = _classify(stderr or "", proc.returncode)
        _trace.log(
            "az.cmd",
            cmd=cmd,
            returncode=proc.returncode,
            kind=kind,
            stderr_len=len(stderr or ""),
        )
        raise AzError(kind, args, stderr or "")
    _trace.log("az.cmd", cmd=cmd, returncode=0, stdout_len=len(stdout or ""))
    return json.loads(stdout) if stdout and stdout.strip() else []


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
