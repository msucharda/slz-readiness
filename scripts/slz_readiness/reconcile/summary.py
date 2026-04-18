"""Emit ``reconcile.summary.{md,json}`` alongside ``mg_alias.json``.

Uses the shared helpers from :mod:`slz_readiness._summary` so reconcile
summaries render with the same header block and table style as the
other four phases. The summary is what the ``/slz-run`` gate
between Reconcile and Evaluate excerpts into its ``ask_user`` form.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import _summary


def write(run_dir: Path, mode: str, alias: dict[str, str | None]) -> dict[str, Any]:
    """Write reconcile.summary.md + .json; return the JSON payload."""
    roles_mapped = sorted([k for k, v in alias.items() if v is not None])
    roles_unmapped = sorted([k for k, v in alias.items() if v is None])
    customer_mgs = sorted({v for v in alias.values() if v is not None})

    payload: dict[str, Any] = {
        "phase": "reconcile",
        "run_id": _summary.run_id_from_path(run_dir),
        "generated_at": _summary.utc_now_iso(),
        "mode": mode,
        "roles_mapped_count": len(roles_mapped),
        "roles_unmapped_count": len(roles_unmapped),
        "unique_customer_mgs_claimed": len(customer_mgs),
        "roles_mapped": roles_mapped,
        "roles_unmapped": roles_unmapped,
        "alias_file": "mg_alias.json",
    }
    _summary.write_json(run_dir / "reconcile.summary.json", payload)

    parts: list[str] = [
        _summary.header_block("Reconcile", run_id=_summary.run_id_from_path(run_dir)),
        "",
        f"- **Mode**: `{mode}`",
        f"- **Roles mapped**: {len(roles_mapped)}",
        f"- **Roles left unmapped** (evaluated as canonical SLZ names): {len(roles_unmapped)}",
        f"- **Unique customer MGs claimed**: {len(customer_mgs)}",
        "",
        "## Role mapping",
        "",
    ]
    if any(v is not None for v in alias.values()):
        parts.append("| role | customer MG |")
        parts.append("| --- | --- |")
        for role in sorted(alias.keys()):
            mapped = alias[role]
            parts.append(f"| `{role}` | {'`' + mapped + '`' if mapped else '_(none)_'} |")
    else:
        parts.append(
            "_All roles left unmapped — greenfield path. Evaluate will run "
            "against the canonical SLZ hierarchy unchanged._"
        )

    parts.extend([
        "",
        "## See also",
        "",
        "- `mg_alias.json` — the alias map consumed by Evaluate",
        "- `docs/brownfield.md` — workarounds when this skeleton isn't enough",
    ])
    _summary.write_md(run_dir / "reconcile.summary.md", "\n".join(parts))
    return payload
