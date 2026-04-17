"""Bump version across manifests, commit, tag, push.

Usage:
    python scripts/release.py <new_version> [--changelog "message"] [--no-push]

Why this exists: apm itself does not orchestrate releases. The plugin's
version string lives in four places and drifted in v0.2.0. This script keeps
them in lock-step and is the single supported release path.

Touches:
    apm.yml                                  (top-level `version:`)
    .github/plugin/plugin.json               (`version`)
    scripts/slz_readiness/__init__.py        (`__version__`)
    data/baseline/VERSIONS.json              (`plugin.version` + changelog)

Then: git add → commit → tag vX.Y.Z → push origin main --tags.
The `release.yml` workflow picks up the tag and publishes the zip.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")
REPO = Path(__file__).resolve().parent.parent


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=REPO, check=check, text=True, capture_output=True)


def bump_apm_yml(v: str) -> None:
    path = REPO / "apm.yml"
    text = path.read_text(encoding="utf-8")
    new = re.sub(r"^version:\s*.*$", f"version: {v}", text, count=1, flags=re.M)
    if new == text:
        raise SystemExit(f"no `version:` line found in {path}")
    path.write_text(new, encoding="utf-8")


def bump_plugin_json(v: str) -> None:
    path = REPO / ".github" / "plugin" / "plugin.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = v
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def bump_init_py(v: str) -> None:
    path = REPO / "scripts" / "slz_readiness" / "__init__.py"
    text = path.read_text(encoding="utf-8")
    new = re.sub(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{v}"', text, count=1)
    if new == text:
        raise SystemExit(f"no `__version__` assignment found in {path}")
    path.write_text(new, encoding="utf-8")


def bump_versions_json(v: str, changelog: str | None) -> None:
    path = REPO / "data" / "baseline" / "VERSIONS.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["plugin"]["version"] = v
    if changelog:
        data["plugin"]["changelog"] = f"v{v} — {changelog}"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def verify_clean_tree() -> None:
    out = run("git", "status", "--porcelain").stdout.strip()
    if out:
        raise SystemExit("working tree is dirty; commit or stash first:\n" + out)


def tag_exists(tag: str) -> bool:
    result = run("git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False)
    return result.returncode == 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("version", help="new semver, e.g. 0.2.1")
    p.add_argument("--changelog", help="short changelog line (stored in VERSIONS.json)")
    p.add_argument("--no-push", action="store_true", help="commit + tag locally only")
    p.add_argument("--allow-dirty", action="store_true", help="skip clean-tree check")
    args = p.parse_args()

    v = args.version.lstrip("v")
    if not SEMVER.match(v):
        raise SystemExit(f"not a valid semver: {v}")
    tag = f"v{v}"
    if tag_exists(tag):
        raise SystemExit(f"tag {tag} already exists")
    if not args.allow_dirty:
        verify_clean_tree()

    bump_apm_yml(v)
    bump_plugin_json(v)
    bump_init_py(v)
    bump_versions_json(v, args.changelog)

    run("git", "add",
        "apm.yml",
        ".github/plugin/plugin.json",
        "scripts/slz_readiness/__init__.py",
        "data/baseline/VERSIONS.json")
    msg = f"release: {tag}" + (f"\n\n{args.changelog}" if args.changelog else "")
    run("git", "commit", "-m", msg)
    run("git", "tag", "-a", tag, "-m", tag)

    if args.no_push:
        print(f"{tag} committed + tagged locally. Push with: git push origin main --tags")
        return 0

    run("git", "push", "origin", "HEAD")
    run("git", "push", "origin", tag)
    print(f"released {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
