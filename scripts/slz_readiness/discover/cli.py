"""`slz-discover` — runs every discover script and writes findings.json."""
from __future__ import annotations

import inspect
import json
import sys
import time
from pathlib import Path

import click

from .. import _trace
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
        sub_filter: set[str] | None = None  # None = no filter downstream
        scope_mode = "all"
        scope_sub_ids: list[str] = sorted(_list_tenant_subscriptions(tenant_id))
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
    click.echo(
        f"Wrote {len(findings)} findings -> {out_path} (total {total_elapsed:.1f}s)"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
