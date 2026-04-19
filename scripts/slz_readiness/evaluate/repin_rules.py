"""Repin rule-YAML baseline SHAs to match the currently vendored baseline.

Why this exists
---------------
Every rule under ``scripts/evaluate/rules/**/*.yml`` pins a ``baseline.sha``
(and, when relevant, ``matcher.archetype_ref.sha``) to the *blob* SHA of a
specific file at the commit recorded in ``data/baseline/VERSIONS.json``.

When the ALZ upstream edits a file and we re-vendor (``vendor_baseline.py``
pulls new blob SHAs from GitHub), the rule pins drift out of sync and
``evaluate.defid_load_skip`` events start firing: every archetype policy
assignment fails to load, gaps go underreported, and scaffold silently
refuses to emit.

This tool reads ``data/baseline/alz-library/_manifest.json`` — which is the
source of truth for the currently vendored tree — and rewrites each rule
YAML's ``sha:`` fields to match. It is idempotent and local-only (no
network), and it is the canonical ``after-revendor`` step.

Usage::

    python -m slz_readiness.evaluate.repin_rules            # report drift
    python -m slz_readiness.evaluate.repin_rules --write    # apply fixes
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_DIR = REPO_ROOT / "scripts" / "evaluate" / "rules"
MANIFEST_FILE = REPO_ROOT / "data" / "baseline" / "alz-library" / "_manifest.json"

# Matches a YAML block (top-level ``baseline:`` OR nested
# ``matcher.archetype_ref:``) of the form::
#
#     baseline:
#       source: https://github.com/Azure/Azure-Landing-Zones-Library
#       path: <path under data/baseline/alz-library/>
#       sha: <40-char hex>
#
# Captures the leading indentation of ``path:`` so we can line the ``sha:``
# up exactly. The YAML loader is deliberately NOT used — regex preserves
# comments, key order, and block style byte-for-byte.
_BLOCK_RE = re.compile(
    r"(?P<prefix>(?:^|\n)(?P<indent>[ \t]*)path:[ \t]*(?P<path>[^\n]+)\n"
    r"(?P=indent)sha:[ \t]*)(?P<sha>[0-9a-f]{40})",
    re.MULTILINE,
)


def _load_manifest() -> dict[str, str]:
    data = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    return {path: meta["git_sha"] for path, meta in data["files"].items()}


def _check_file(path: Path, manifest: dict[str, str]) -> list[tuple[str, str, str]]:
    """Return a list of (referenced_path, pinned_sha, vendored_sha) drifts."""
    drifts: list[tuple[str, str, str]] = []
    text = path.read_text(encoding="utf-8")
    for m in _BLOCK_RE.finditer(text):
        ref_path = m.group("path").strip()
        pinned = m.group("sha")
        expected = manifest.get(ref_path)
        if expected is None:
            drifts.append((ref_path, pinned, "<not-vendored>"))
        elif expected != pinned:
            drifts.append((ref_path, pinned, expected))
    return drifts


def _rewrite_file(path: Path, manifest: dict[str, str]) -> int:
    text = path.read_text(encoding="utf-8")
    n = 0

    def sub(match: re.Match[str]) -> str:
        nonlocal n
        ref_path = match.group("path").strip()
        expected = manifest.get(ref_path)
        if expected is None or expected == match.group("sha"):
            return match.group(0)
        n += 1
        return match.group("prefix") + expected

    new_text = _BLOCK_RE.sub(sub, text)
    if n:
        path.write_text(new_text, encoding="utf-8")
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply fixes in-place. Without this flag, reports drift and exits non-zero.",
    )
    args = parser.parse_args()

    if not MANIFEST_FILE.exists():
        print(f"Manifest missing: {MANIFEST_FILE}", file=sys.stderr)
        print(
            "Run `python -m slz_readiness.evaluate.vendor_baseline --force` first.",
            file=sys.stderr,
        )
        return 2

    manifest = _load_manifest()
    yaml_files = sorted(RULES_DIR.rglob("*.yml"))

    total_drift = 0
    total_unvendored = 0
    total_repinned = 0
    for yaml_file in yaml_files:
        drifts = _check_file(yaml_file, manifest)
        if not drifts:
            continue
        rel = yaml_file.relative_to(REPO_ROOT)
        for ref_path, pinned, expected in drifts:
            if expected == "<not-vendored>":
                total_unvendored += 1
                print(f"UNVENDORED  {rel}: {ref_path} (pinned {pinned[:8]}… — not in manifest)")
            else:
                total_drift += 1
                print(f"DRIFT       {rel}: {ref_path} ({pinned[:8]}… -> {expected[:8]}…)")
        if args.write:
            total_repinned += _rewrite_file(yaml_file, manifest)

    print()
    if args.write:
        print(f"Repinned {total_repinned} sha(s) across {len(yaml_files)} rule file(s).")
        if total_unvendored:
            print(f"WARNING: {total_unvendored} reference(s) point at paths not in the manifest.")
            print(
                "         These require a proper re-vendor (vendor_baseline.py) "
                "or a baseline path fix."
            )
        return 1 if total_unvendored else 0
    else:
        summary = f"{total_drift} drift(s), {total_unvendored} unvendored reference(s)."
        if total_drift or total_unvendored:
            print(f"{summary} Re-run with --write to repin.")
            return 1
        print(f"{summary} No action needed.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
