"""`slz-discover` — runs every discover script and writes findings.json."""
from __future__ import annotations

import inspect
import json
import sys
import time
from pathlib import Path

import click

from .. import _summary, _trace
from . import (
    _progress,
    identity_rbac,
    logging_monitoring,
    mg_hierarchy,
    policy_assignments,
    sovereignty_controls,
    subscription_inventory,
)
from .az_common import AzError, run_az

DISCOVERERS = [
    mg_hierarchy,
    subscription_inventory,
    policy_assignments,
    identity_rbac,
    logging_monitoring,
    sovereignty_controls,
]


def _short_name(mod) -> str:
    return mod.__name__.rsplit(".", 1)[-1]


def _write_stage(stages_dir: Path, name: str, findings: list) -> None:
    stages_dir.mkdir(parents=True, exist_ok=True)
    path = stages_dir / f"{name}.json"
    try:
        path.write_text(
            json.dumps({"findings": findings}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        _trace.log("stage.write_error", path=str(path), error=str(exc))


def _call_discover(mod, progress_cb, subscription_filter):
    """Invoke ``mod.discover()`` passing optional kwargs if supported."""
    sig = inspect.signature(mod.discover)
    kwargs = {}
    if "progress_cb" in sig.parameters:
        kwargs["progress_cb"] = progress_cb
    if "subscription_filter" in sig.parameters and subscription_filter is not None:
        kwargs["subscription_filter"] = subscription_filter
    return mod.discover(**kwargs)


def _resolve_active_tenant() -> str | None:
    """Return the currently-active tenant id per ``az account show`` (or None)."""
    try:
        info = run_az(["account", "show"])
    except AzError:
        return None
    if isinstance(info, dict):
        return info.get("tenantId")
    return None


def _list_tenant_subscriptions(tenant_id: str) -> list[str]:
    """Return every subscription id visible in ``tenant_id``."""
    try:
        subs = run_az(["account", "list", "--all"])
    except AzError:
        return []
    out: list[str] = []
    for sub in subs or []:
        if (sub.get("tenantId") or "").lower() != tenant_id.lower():
            continue
        sid = sub.get("id") or sub.get("subscriptionId")
        if sid:
            out.append(sid)
    return out


@click.command()
@click.option("--out", "out_path", required=True, type=click.Path(path_type=Path))
@click.option(
    "--tenant",
    "tenant_id",
    required=True,
    help="Azure tenant id (UUID) this run targets. Must match the active az account.",
)
@click.option(
    "--subscription",
    "subscription_ids",
    multiple=True,
    help="Subscription id to scope sub-level checks to. Repeat for multiple. "
    "Mutually exclusive with --all-subscriptions.",
)
@click.option(
    "--all-subscriptions",
    "all_subscriptions",
    is_flag=True,
    default=False,
    help="Explicitly sweep every subscription in the tenant. Required when no "
    "--subscription flags are given (prevents silent fan-outs).",
)
def main(
    out_path: Path,
    tenant_id: str,
    subscription_ids: tuple[str, ...],
    all_subscriptions: bool,
) -> None:
    """Collect read-only findings from the tenant into findings.json."""
    # --- Scope validation ------------------------------------------------
    if subscription_ids and all_subscriptions:
        raise click.UsageError(
            "--subscription and --all-subscriptions are mutually exclusive."
        )
    if not subscription_ids and not all_subscriptions:
        raise click.UsageError(
            "Pick a scope explicitly: pass one or more --subscription <id> "
            "(repeatable) or --all-subscriptions to sweep every subscription "
            "in the tenant. Refusing to fan out silently."
        )
    active_tenant = _resolve_active_tenant()
    if active_tenant is None:
        raise click.ClickException(
            "Could not read active tenant via `az account show`. "
            "Run `az login --tenant <id>` and retry."
        )
    if active_tenant.lower() != tenant_id.lower():
        raise click.ClickException(
            f"Active az tenant is {active_tenant}, but --tenant was {tenant_id}. "
            f"Run `az login --tenant {tenant_id}` and retry."
        )

    if all_subscriptions:
        scope_mode = "all"
        scope_sub_ids: list[str] = sorted(_list_tenant_subscriptions(tenant_id))
        # Pin the filter to the tenant-scoped sub set so downstream discoverers
        # that call `az account list --all` (which spans every tenant the user
        # is a guest in) cannot silently fan out cross-tenant. Fall back to
        # None only when the tenant genuinely has zero subs, so discoverers
        # still emit tenant-level error findings instead of short-circuiting.
        sub_filter: set[str] | None = set(scope_sub_ids) if scope_sub_ids else None
    else:
        sub_filter = {s for s in subscription_ids}
        scope_mode = "filtered"
        scope_sub_ids = sorted(sub_filter)

    run_scope = {
        "tenant_id": tenant_id,
        "mode": scope_mode,
        "subscription_ids": scope_sub_ids,
    }

    # --- Run -------------------------------------------------------------
    findings: list = []
    run_dir = out_path.parent
    stages_dir = run_dir / "stages"
    module_records: list[dict] = []
    total_start = time.monotonic()

    click.echo(
        f"Scope: tenant={tenant_id} mode={scope_mode} "
        f"subscriptions={len(scope_sub_ids)}",
        err=True,
    )

    with _trace.tracer(run_dir, phase="discover"):
        _trace.log("run.scope", **run_scope)
        for mod in DISCOVERERS:
            name = _short_name(mod)
            _trace.log("discoverer.begin", module=mod.__name__)
            click.echo(f"▶ {name} ...", err=True, nl=True)
            sys.stderr.flush()
            t0 = time.monotonic()
            try:
                mod_findings = _call_discover(
                    mod,
                    _progress.make_callback(name),
                    sub_filter,
                )
            except Exception as exc:  # noqa: BLE001
                elapsed = time.monotonic() - t0
                _trace.log(
                    "discoverer.error",
                    module=mod.__name__,
                    error=str(exc),
                    elapsed_sec=round(elapsed, 2),
                )
                click.echo(
                    f"✗ {name} — error in {elapsed:.1f}s: {exc}", err=True
                )
                sys.stderr.flush()
                module_records.append(
                    {
                        "name": name,
                        "status": "error",
                        "finding_count": 0,
                        "error_count": 0,
                        "elapsed_sec": round(elapsed, 2),
                        "crash": str(exc)[:200],
                        "error_kinds": [],
                    }
                )
                continue
            elapsed = time.monotonic() - t0
            _trace.log(
                "discoverer.end",
                module=mod.__name__,
                finding_count=len(mod_findings),
                elapsed_sec=round(elapsed, 2),
            )
            click.echo(
                f"✓ {name} — {len(mod_findings)} findings in {elapsed:.1f}s",
                err=True,
            )
            sys.stderr.flush()
            _write_stage(stages_dir, name, mod_findings)
            findings.extend(mod_findings)
            errs = _summary.error_findings(mod_findings)
            error_kinds = sorted({
                (f.get("observed_state") or {}).get("error", "")
                for f in errs
                if (f.get("observed_state") or {}).get("error")
            })
            if not errs:
                status = "ok"
            elif len(errs) == len(mod_findings):
                status = "error"
            else:
                status = "partial"
            module_records.append(
                {
                    "name": name,
                    "status": status,
                    "finding_count": len(mod_findings),
                    "error_count": len(errs),
                    "elapsed_sec": round(elapsed, 2),
                    "error_kinds": error_kinds,
                }
            )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {"run_scope": run_scope, "findings": findings},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    total_elapsed = time.monotonic() - total_start
    # --- Human-readable summary ------------------------------------------
    with _trace.tracer(run_dir, phase="discover"):
        _write_discover_summary(
            run_dir=run_dir,
            run_scope=run_scope,
            findings=findings,
            module_records=module_records,
            total_elapsed=round(total_elapsed, 2),
        )
    click.echo(
        f"Wrote {len(findings)} findings -> {out_path} (total {total_elapsed:.1f}s)"
    )
    sys.exit(0)


def _top_observations(findings: list[dict]) -> list[str]:
    """Deterministic one-liners distilled from the raw findings list.

    Ordering matches the discoverer execution order so the bullet list tells a
    consistent story. Counts never include error findings — those are surfaced
    under "Caveats".
    """
    lines: list[str] = []
    # Management groups
    mg_findings = [
        f for f in findings
        if f.get("resource_type") == "microsoft.management/managementgroups.summary"
        and not (isinstance(f.get("observed_state"), dict) and "error" in f["observed_state"])
    ]
    if mg_findings:
        present_ids = sorted(
            (mg_findings[0].get("observed_state") or {}).get("present_ids") or []
        )
        lines.append(f"Management groups present: {len(present_ids)}")
    # Subscriptions
    sub_findings = [
        f for f in findings
        if f.get("resource_type") == "microsoft.resources/subscriptions.summary"
    ]
    if sub_findings:
        count = len(
            (sub_findings[0].get("observed_state") or {}).get("subscriptions") or []
        )
        lines.append(f"Subscriptions observed: {count}")
    # Policy assignments
    pa_scopes = sorted({
        f.get("scope", "")
        for f in findings
        if f.get("resource_type") == "microsoft.authorization/policyassignments"
        and not (isinstance(f.get("observed_state"), dict) and "error" in f["observed_state"])
    })
    if pa_scopes:
        lines.append(f"Policy assignment scopes inspected: {len(pa_scopes)}")
    # Log Analytics workspaces
    la_findings = [
        f for f in findings
        if f.get("resource_type") == "microsoft.operationalinsights/workspaces"
        and not (isinstance(f.get("observed_state"), dict) and "error" in f["observed_state"])
    ]
    if la_findings:
        workspaces = sum(
            len((f.get("observed_state") or {}).get("workspaces") or [])
            for f in la_findings
        )
        lines.append(
            f"Log Analytics workspaces observed: {workspaces} "
            f"(across {len(la_findings)} subscription-level query/queries)"
        )
    # Sovereignty controls
    sov_findings = [
        f for f in findings
        if f.get("resource_type") == "microsoft.policyinsights/policystates"
        and not (isinstance(f.get("observed_state"), dict) and "error" in f["observed_state"])
    ]
    if sov_findings:
        non_compliant = sum(
            (f.get("observed_state") or {}).get("nonCompliantCount") or 0
            for f in sov_findings
        )
        lines.append(
            f"Sovereignty policy-state queries: {len(sov_findings)} "
            f"(non-compliant rows: {non_compliant})"
        )
    return lines


def _write_discover_summary(
    *,
    run_dir: Path,
    run_scope: dict,
    findings: list[dict],
    module_records: list[dict],
    total_elapsed: float,
) -> None:
    """Emit ``discover.summary.{json,md}`` next to findings.json."""
    errs = _summary.error_findings(findings)
    payload = {
        "phase": "discover",
        "tenant_id": run_scope.get("tenant_id"),
        "mode": run_scope.get("mode"),
        "subscription_count": len(run_scope.get("subscription_ids") or []),
        "total_elapsed_sec": total_elapsed,
        "finding_count": len(findings),
        "error_finding_count": len(errs),
        "modules": module_records,
        "top_observations": _top_observations(findings),
        "caveats": [
            {
                "resource_type": f.get("resource_type"),
                "resource_id": f.get("resource_id"),
                "error": (f.get("observed_state") or {}).get("error"),
                "message": (f.get("observed_state") or {}).get("message", "")[:200],
            }
            for f in errs
        ],
    }
    _summary.write_json(run_dir / "discover.summary.json", payload)

    parts: list[str] = []
    parts.append(
        _summary.header_block(
            "SLZ Discover summary",
            tenant=run_scope.get("tenant_id"),
            run_id=_summary.run_id_from_path(run_dir),
            mode=str(run_scope.get("mode")),
            extra={"subs": str(len(run_scope.get("subscription_ids") or []))},
        )
    )
    parts.append("## Modules")
    parts.append("")
    glyph = {"ok": _summary.GLYPH_OK, "partial": _summary.GLYPH_WARN, "error": _summary.GLYPH_FAIL}
    parts.append(
        _summary.render_table(
            ["Module", "Status", "Findings", "Errors", "Elapsed (s)"],
            [
                [
                    m["name"],
                    f"{glyph.get(m['status'], m['status'])} {m['status']}",
                    m["finding_count"],
                    m["error_count"],
                    f"{m['elapsed_sec']:.1f}",
                ]
                for m in module_records
            ],
        )
    )
    parts.append("")
    parts.append(f"**Total:** {len(findings)} findings in {total_elapsed:.1f}s")
    parts.append("")
    obs = _top_observations(findings)
    if obs:
        parts.append("## Top observations")
        parts.append("")
        for line in obs:
            parts.append(f"- {line}")
        parts.append("")
    if errs:
        parts.append("## Caveats (discovery errors)")
        parts.append("")
        parts.append(
            "These findings could not be evaluated; `slz-evaluate` will surface "
            "them as `status: unknown` gaps."
        )
        parts.append("")
        parts.append(
            _summary.render_table(
                ["Resource", "Error kind", "Message"],
                [
                    [
                        f.get("resource_id", ""),
                        (f.get("observed_state") or {}).get("error", ""),
                        (f.get("observed_state") or {}).get("message", "")[:120],
                    ]
                    for f in errs
                ],
            )
        )
        parts.append("")
    parts.append("## See also")
    parts.append("")
    parts.append("- `findings.json` — raw findings")
    parts.append("- `stages/` — per-module debug artifacts")
    parts.append("- `trace.jsonl` — full audit trail")
    _summary.write_md(run_dir / "discover.summary.md", "\n".join(parts))
    _trace.log(
        "discover.summary",
        finding_count=len(findings),
        error_finding_count=len(errs),
        module_count=len(module_records),
    )


if __name__ == "__main__":
    main()
