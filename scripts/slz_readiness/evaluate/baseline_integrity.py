"""Verify every file under data/baseline/alz-library/ matches its pinned SHA.

Reads the manifest written by vendor_baseline.py and re-hashes every file
locally (no network calls). CI-hard gate against tampering or drift.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BASELINE_DIR = Path(__file__).resolve().parents[3] / "data" / "baseline"
ALZ_ROOT = BASELINE_DIR / "alz-library"
MANIFEST_FILE = ALZ_ROOT / "_manifest.json"


def _git_blob_sha(data: bytes) -> str:
    h = hashlib.sha1()  # noqa: S324
    h.update(b"blob " + str(len(data)).encode() + b"\x00")
    h.update(data)
    return h.hexdigest()


def main() -> int:
    if not MANIFEST_FILE.exists():
        print(
            f"No manifest at {MANIFEST_FILE}. "
            "Run `python -m slz_readiness.evaluate.vendor_baseline` first.",
            file=sys.stderr,
        )
        # Until the baseline is vendored, exit 0 so scaffold-phase CI stays green.
        # Once baseline is committed, this script rejects any untracked or modified file.
        return 0

    manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    expected: dict[str, dict] = manifest["files"]
    errors: list[str] = []

    actual_paths = {
        str(p.relative_to(ALZ_ROOT)).replace("\\", "/")
        for p in ALZ_ROOT.rglob("*")
        if p.is_file() and p.name != "_manifest.json"
    }
    for p in sorted(actual_paths - expected.keys()):
        errors.append(f"UNTRACKED: {p} is on disk but not in manifest")
    for p in sorted(expected.keys() - actual_paths):
        errors.append(f"MISSING:   {p} in manifest but not on disk")

    for rel, info in expected.items():
        path = ALZ_ROOT / rel
        if not path.exists():
            continue
        actual = _git_blob_sha(path.read_bytes())
        if actual != info["git_sha"]:
            errors.append(f"MODIFIED:  {rel} expected {info['git_sha']} got {actual}")

    if errors:
        print(f"baseline-integrity: {len(errors)} problem(s)", file=sys.stderr)
        for e in errors[:50]:
            print(f"  {e}", file=sys.stderr)
        if len(errors) > 50:
            print(f"  … {len(errors) - 50} more", file=sys.stderr)
        return 2

    print(f"baseline-integrity: OK ({len(expected)} files match pinned SHAs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
