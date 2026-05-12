# Release Process

## At a glance

| Attribute | Value |
|---|---|
| Release script | [`scripts/release.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/release.py) |
| CI workflow | [`.github/workflows/release.yml`](https://github.com/msucharda/slz-readiness/blob/main/.github/workflows/release.yml) |
| Current version | `0.14.8` |
| Version files | 5 — all kept in lock-step |
| Cadence | Tag-driven; no fixed schedule |

## The five version strings

| File | Field | Role |
|---|---|---|
| [`apm.yml`](https://github.com/msucharda/slz-readiness/blob/main/apm.yml) | `version:` (line 2) | Dev/source plugin manifest |
| [`.github/plugin/plugin.json`](https://github.com/msucharda/slz-readiness/blob/main/.github/plugin/plugin.json) | `"version"` | Packaged/published manifest |
| [`scripts/slz_readiness/__init__.py`](https://github.com/msucharda/slz-readiness/blob/main/scripts/slz_readiness/__init__.py) | `__version__` (line 7) | Python runtime |
| [`data/baseline/VERSIONS.json`](https://github.com/msucharda/slz-readiness/blob/main/data/baseline/VERSIONS.json) | `plugin` | Audit/baseline correlation |
| [`pyproject.toml`](https://github.com/msucharda/slz-readiness/blob/main/pyproject.toml) | `version` | Python package metadata |

All five must agree. Any PR that edits one without the others fails CI.

## The release flow

```mermaid
sequenceDiagram
    autonumber
    actor Maint as Maintainer
    participant Rel as scripts/release.py
    participant Files as 5 version files
    participant Git as local git
    participant GH as GitHub
    participant CI as release.yml

    Maint->>Rel: python scripts/release.py 0.14.9
    Rel->>Files: write 0.14.9 to all five
    Rel->>Git: commit "release: v0.14.9"
    Rel->>Git: tag v0.14.9
    Rel->>GH: push commit + tag
    GH->>CI: release.yml triggers on tag
    CI->>CI: read v0.14.9 from tag
    CI->>CI: assert all 5 version files = 0.14.9
    alt mismatch
        CI-->>Maint: fail — version drift
    else green
        CI->>GH: publish release + plugin artifact
    end
```

<!-- Source: scripts/release.py, .github/workflows/release.yml -->

## Why lockstep

The version strings serve different audiences but must correlate:

- `apm.yml` / `plugin.json` — Copilot CLI users see this via `/plugin list`.
- `__version__` — `slz-discover --version` shows this; embedded in `findings.json`.
- `pyproject.toml` — Python package metadata for editable/dev installs.
- `VERSIONS.json` — audit trail: "this run was plugin 0.14.8 against ALZ SHA X".

A correctness bug in any of these makes audit evidence ambiguous. The lockstep rule means a consumer reading any one field can trust the others.

## `release.py` behaviour

Invocations:

- `python scripts/release.py 0.14.9` — explicit target.
- `python scripts/release.py 0.14.9 --changelog "summary"` — store a short changelog line in `VERSIONS.json`.

What it does:

1. Validates the target semver and clean working tree.
2. Rewrites all 5 files with deterministic formatting.
3. Updates `VERSIONS.json` plugin metadata, including optional changelog.
4. Commits the version bump.
5. Creates an annotated `vX.Y.Z` tag.
6. Pushes commit and tag unless `--no-push` is supplied.

Use `--no-push` when you want to inspect the local commit and tag before publishing.

## CI cross-check

`release.yml` on tag push does essentially:

```bash
TAG="${GITHUB_REF#refs/tags/v}"   # 0.14.9

# all five must equal $TAG
grep "version: $TAG" apm.yml
jq -e --arg v "$TAG" '.version == $v' .github/plugin/plugin.json
grep "__version__ = \"$TAG\"" scripts/slz_readiness/__init__.py
jq -e --arg v "$TAG" '.plugin.version == $v' data/baseline/VERSIONS.json
grep "version = \"$TAG\"" pyproject.toml
```

Any failure aborts the release. Since all five are updated by `release.py` in one commit, this is really catching "maintainer edited one by hand" rather than "release.py is broken".

## Baseline bump ≠ version bump

A baseline SHA bump (new ALZ Library release) is independent of a plugin version bump:

| Scenario | Bump plugin version? |
|---|---|
| Baseline SHA update only (no rule changes) | Patch |
| New rule added | Minor |
| Matcher or template API break | Major |
| Copy-edit docs only | No release |

## Emergency process

If a critical bug is found after tag:

1. Revert the tag locally (`git tag -d vX.Y.Z; git push --delete origin vX.Y.Z`).
2. Fix on a branch.
3. Re-run `python scripts/release.py X.Y.Z+1` (never re-use a tag — consumers may have cached).
4. Tag and push the new version.

## Related reading

- [Plugin Mechanics](/deep-dive/plugin-mechanics) — `apm.yml` vs `plugin.json`.
- [Baseline Vendoring](/deep-dive/evaluate/baseline-vendoring) — when baseline updates trigger a plugin bump.
- [Testing](/deep-dive/testing) — the CI jobs that gate every release.
