"""Discover sovereignty-relevant signals (v1 MVP: re-use policy_assignments).

This file exists so the rule engine can key sovereignty rules to a specific
discover script in future iterations (e.g. CMK usage, Confidential VM SKUs).
For v1 the data flows through policy_assignments.py.
"""
from __future__ import annotations

from typing import Any


def discover() -> list[dict[str, Any]]:
    return []
