"""Vendor the ALZ baseline at the SHA pinned in data/baseline/VERSIONS.json.

Downloads every blob from the configured subtrees of the upstream repo at the
exact commit SHA and writes them under data/baseline/alz-library/. Also
records each file's git-blob SHA in data/baseline/alz-library/_manifest.json
so baseline_integrity can re-verify offline.

Usage:
    python -m slz_readiness.evaluate.vendor_baseline [--force]

The tool is read-only against Azure resources — it only talks to GitHub's
public API and raw.githubusercontent.com. No Azure credentials required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASELINE_DIR = Path(__file__).resolve().parents[3] / "data" / "baseline"
VERSIONS_FILE = BASELINE_DIR / "VERSIONS.json"
ALZ_ROOT = BASELINE_DIR / "alz-library"
MANIFEST_FILE = ALZ_ROOT / "_manifest.json"

GITHUB_API = "https://api.github.com"
USER_AGENT = "slz-readiness/0.1 (+https://github.com/sovereign-cloud-day/slz-readiness)"


def _http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req) as resp:  # noqa: S310 - fixed github.com URL
        return json.load(resp)


def _http_get_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req) as resp:  # noqa: S310 - fixed raw.githubusercontent.com URL
        return resp.read()


def _git_blob_sha(data: bytes) -> str:
    """Compute the git blob SHA-1 (for parity with GitHub's tree API)."""
    h = hashlib.sha1()  # noqa: S324 - git uses SHA-1 for blob hashes
    h.update(b"blob " + str(len(data)).encode() + b"\x00")
    h.update(data)
    return h.hexdigest()


def _walk_tree(owner: str, repo: str, tree_sha: str, prefix: str = "") -> list[dict[str, str]]:
    data = _http_get_json(f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{tree_sha}?recursive=1")
    if data.get("truncated"):
        raise RuntimeError(f"Tree {tree_sha} was truncated by GitHub API; implement non-recursive walk")
    blobs: list[dict[str, str]] = []
    for entry in data["tree"]:
        if entry["type"] != "blob":
            continue
        blobs.append({"path": f"{prefix}{entry['path']}", "sha": entry["sha"], "size": entry.get("size", 0)})
    return blobs


def _resolve_subtree(owner: str, repo: str, commit_sha: str, subtree_path: str) -> str:
    """Resolve a path under a commit tree to its subtree SHA."""
    current = _http_get_json(f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{commit_sha}")["sha"]
    for part in subtree_path.split("/"):
        tree = _http_get_json(f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{current}")
        match = next((e for e in tree["tree"] if e["path"] == part and e["type"] == "tree"), None)
        if not match:
            raise FileNotFoundError(f"{subtree_path} not found under {owner}/{repo}@{commit_sha}")
        current = match["sha"]
    return current


def vendor(force: bool = False) -> int:
    versions = json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))
    alz = versions["alz_library"]
    if alz["sha"] == "TBD":
        print("VERSIONS.json still has TBD SHA; populate it first.", file=sys.stderr)
        return 2

    owner, repo = alz["source"].rstrip("/").split("/")[-2:]
    commit_sha = alz["sha"]
    manifest: dict[str, Any] = {"commit_sha": commit_sha, "source": alz["source"], "files": {}}

    if ALZ_ROOT.exists() and any(ALZ_ROOT.iterdir()) and not force:
        print(f"{ALZ_ROOT} already populated; pass --force to re-vendor.")
        return 0

    for subtree in alz["subtrees"]:
        print(f"Resolving {subtree}@{commit_sha[:8]} …")
        subtree_sha = _resolve_subtree(owner, repo, commit_sha, subtree)
        blobs = _walk_tree(owner, repo, subtree_sha, prefix=f"{subtree}/")
        print(f"  {len(blobs)} blobs")
        for blob in blobs:
            rel = blob["path"]
            dest = ALZ_ROOT / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{commit_sha}/{rel}"
            try:
                data = _http_get_bytes(raw_url)
            except urllib.error.HTTPError as e:
                print(f"    ! {rel}: {e}", file=sys.stderr)
                continue
            actual = _git_blob_sha(data)
            if actual != blob["sha"]:
                raise RuntimeError(f"SHA mismatch on {rel}: expected {blob['sha']} got {actual}")
            dest.write_bytes(data)
            manifest["files"][rel] = {"git_sha": blob["sha"], "size": len(data)}

    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Vendored {len(manifest['files'])} files -> {ALZ_ROOT}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Re-vendor even if baseline already exists.")
    args = parser.parse_args()
    return vendor(force=args.force)


if __name__ == "__main__":
    sys.exit(main())
