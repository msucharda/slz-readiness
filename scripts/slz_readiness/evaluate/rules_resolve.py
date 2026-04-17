"""Validates every rule YAML resolves to a vendored baseline file@sha."""
from __future__ import annotations

import sys

from .loaders import RULES_DIR, RuleLoadError, load_all_rules


def main() -> int:
    try:
        rules = load_all_rules()
    except RuleLoadError as e:
        print(f"rules-resolve: {e}", file=sys.stderr)
        return 2
    count = len(rules)
    if count == 0:
        print(f"rules-resolve: no rules found under {RULES_DIR} (OK for scaffold milestone)")
        return 0
    print(f"rules-resolve: OK ({count} rules resolve to baseline files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
