# AGENTS.md — `tests/`

Test suite for `slz-readiness`. See [root AGENTS.md](../AGENTS.md) for repo-wide rules.

## Running

```bash
pytest -q                            # everything
pytest tests/unit/test_evaluate_golden.py  # golden only
pytest -k hooks                      # just hook tests
```

Three-OS matrix in CI — run on Linux locally if possible, but cross-platform code (`discover/az_common.py`, path handling) must be tested on Windows before merge if you touch those areas.

## Layout

```
tests/
├── unit/
│   ├── test_discover_scope.py   # Click CLI flag validation
│   ├── test_evaluate_golden.py  # findings.json -> gaps.json byte-compare
│   └── test_scaffold.py         # emission + skip paths
├── test_hooks.py                # parametrised verb allowlist/denylist
└── fixtures/                    # JSON fixtures for golden
```

## The golden fixture pattern

`test_evaluate_golden.py` loads `tests/fixtures/findings.json`, runs the engine, and compares against `tests/fixtures/gaps.json`. If you change:

- A matcher implementation
- A rule YAML's `target` / `matcher` / `baseline_ref`
- The sort order in the engine

...you must regenerate `gaps.json`. Do it explicitly (save runtime output, review the diff, commit both) — never let a test "auto-update" itself.

## Hook tests

[`test_hooks.py`](test_hooks.py) parametrises the verb allow/deny matrix. Any new allow or deny verb must come with a matching test row. This is a security surface — resist compression.

## Scaffold skip-path tests

[`unit/test_scaffold.py`](unit/test_scaffold.py) covers:

- Happy path (rule → template emission).
- `gap.status == "unknown"` → no emission.
- Unknown `rule_id` → no emission + `scaffold.skipped` trace event.
- Per-scope dedup across multiple gaps.
- Schema validation failure raises (not skips).

When adding a template, add at least one test here.

## What we don't test

- Live Azure discovery (no CI tenants).
- LLM plan quality (non-deterministic).
- Copilot CLI host behaviour (external).

## Boundaries

- Tests must not make network calls. Mock or fixture everything.
- Tests must not write outside `pytest`'s tmp_path fixtures.
- Do not soften assertions to make a test pass — if the engine changed, update the fixture.
