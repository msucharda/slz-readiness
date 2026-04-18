# AGENTS.md — `scripts/`

Python package + supporting assets. See the [root AGENTS.md](../AGENTS.md) for repo-wide context.

## Layout

```
scripts/
├── slz_readiness/          # installable Python package
│   ├── __init__.py         # __version__ lives here
│   ├── _trace.py           # NDJSON tracer (ContextVar)
│   ├── discover/           # phase 1
│   ├── evaluate/           # phase 2
│   └── scaffold/           # phase 4
├── evaluate/
│   └── rules/              # 14 rule YAMLs (data, not code)
├── scaffold/
│   ├── avm_templates/      # 7 Bicep templates
│   └── param_schemas/      # JSON Schemas per template
└── release.py              # version-bump script
```

Plan-phase logic lives in [`.github/skills/plan/SKILL.md`](../.github/skills/plan/SKILL.md) — no Python; the LLM does the narration and `hooks/post_tool_use.py` enforces the structure.

## Package conventions

- Type-hint everything. mypy is in CI.
- Public functions get docstrings; private (`_name`) don't have to.
- Never `subprocess.run(["az", ...])` directly — use `discover/az_common.run_az()`.
- Never use `time.time()` or non-deterministic randomness in `evaluate/` — the engine is golden-tested.
- Log/trace via `_trace.trace_event(...)` — never `print()` to stdout (reserved for JSON output).

## Adding a discoverer

1. New module `slz_readiness/discover/<area>.py` exposing `def discover(scope) -> list[Finding]`.
2. Register in `DISCOVERERS` at [`discover/cli.py:24-31`](slz_readiness/discover/cli.py).
3. Only call `run_az()`; classify errors via `AzError.kind`.
4. Extend `tests/unit/test_discover_scope.py` if new CLI flags are introduced.

## Adding a matcher

1. New `_matcher_name(spec, findings) -> (bool, dict)` in `evaluate/matchers.py`.
2. Register in `MATCHERS` dict (line 98 of `matchers.py`).
3. Parametrised unit test covering pass, fail, and error-finding paths.
4. Document `observed` shape — plan prompt relies on it.

## Adding a template

See [root AGENTS.md](../AGENTS.md) "Adding a template" — touches `scaffold/template_registry.py`, `scaffold/avm_templates/`, `scaffold/param_schemas/`.

## Running locally

```bash
pip install -e ../.[dev]

# each phase in isolation
slz-discover  --tenant <guid> --subscription <sub> --run-id local
slz-evaluate  --run-id local
slz-scaffold  --run-id local

# integrity check for vendored baseline
python -m slz_readiness.evaluate.baseline_integrity
```

## Boundaries

- Do not import `requests` or any HTTP client in the package — we shell out to `az` for a reason.
- Do not import LLM SDKs in `evaluate/` or `scaffold/`.
- Do not read `data/baseline/alz-library/` without going through `loaders.resolve_sha()`.
