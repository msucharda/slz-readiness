# slz-readiness

> Audit an Azure tenant for **Sovereign Landing Zone (SLZ)** readiness against a vendored, SHA-pinned Cloud Adoption Framework baseline.

A Copilot plugin that helps Azure customers check their landing zone against the CAF/ALZ baseline plus SLZ sovereignty overlay, and scaffolds [Azure Verified Modules (AVM)](https://aka.ms/avm) Bicep for the gaps — **read-only**, **deterministic**, and **human-in-the-loop** by design.

## How it works

Four phases, each one small and verifiable on its own:

```
Discover   → read-only az / Azure MCP queries          → artifacts/<run>/findings.json
Evaluate   → pure Python rule engine (NO LLM)          → artifacts/<run>/gaps.json
Plan       → LLM + sequential-thinking, cites rule ids → artifacts/<run>/plan.md
Scaffold   → AVM templates filled with params (no free-form) → artifacts/<run>/bicep/
```

The plugin **never** deploys anything. You review the Bicep, run `az deployment ... what-if`, and apply in your own pipeline.

## Install

**Primary — direct from GitHub:**

```
copilot
/plugin install msucharda/slz-readiness
```

Copilot fetches the repo, reads `.github/plugin/plugin.json`, and registers the agent, skills, slash commands, hooks, and MCP servers. The five slash commands (`/slz-discover`, `/slz-evaluate`, `/slz-plan`, `/slz-scaffold`, `/slz-run`) appear in the `/` menu.

**Requirements (all platforms):** Python 3.11+, `az` CLI (logged in), `git`. No `pip install` is needed for normal use — the skills invoke the engine via `python -m slz_readiness.<phase>.cli`. Works on Windows, macOS and Linux without WSL.

> Replace `msucharda/slz-readiness` with your fork if you've forked this repo.

**Optional — as a marketplace:**

```
/plugin marketplace add msucharda/slz-readiness
/plugin install slz-readiness@slz-readiness
```

**Dev / from local clone (contributors only):**

```bash
git clone https://github.com/msucharda/slz-readiness.git
cd slz-readiness
pip install -e ".[dev]"           # installs slz-* console scripts + test deps
```

Then inside Copilot CLI, from the repo root:

```
/plugin install .
```

**From a packed release (once a `v*` tag is published):**

Download `slz-readiness-vX.Y.Z.zip` from [Releases](./releases), unzip it somewhere, then:

```
/plugin install /absolute/path/to/unpacked/slz-readiness
```

## Usage

```
/slz-run            # end-to-end, pauses for approval between phases (default)
/slz-discover       # phase 1 only
/slz-evaluate       # phase 2 only (deterministic)
/slz-plan           # phase 3 only
/slz-scaffold       # phase 4 only
```

## Anti-hallucination contract

Every claim the plugin makes is backed by a rule YAML that cites a specific file in `data/baseline/` at a specific SHA. If the baseline file can't be resolved, CI fails. See [docs/anti-hallucination.md](./docs/anti-hallucination.md).

## Releasing (maintainers)

Version strings live in four files (`apm.yml`, `.github/plugin/plugin.json`, `scripts/slz_readiness/__init__.py`, `data/baseline/VERSIONS.json`). Use the release script — do not bump by hand:

```bash
python scripts/release.py 0.3.0 --changelog "short summary"
```

The script bumps all four files, commits, tags `vX.Y.Z`, and pushes. The `release.yml` workflow verifies that the tag matches every manifest version and refuses to publish on mismatch, then builds and attaches `slz-readiness-vX.Y.Z.zip` to the GitHub Release.

Consumers install with `/plugin install msucharda/slz-readiness` inside the Copilot CLI (not `apm install` — APM expects a `.apm/` tree this plugin does not ship).

## License

MIT. See [LICENSE](./LICENSE).
