"""Golden test: discover.mg_hierarchy → reconcile.proposer over a real
3-level MG hierarchy fixture.

Regression guard for two bugs that shipped in v0.9.x and caused the
2026-04-19 slz-demo run to flatten the MG graph and mis-map
``slz → sucharda``:

1. ``discover/mg_hierarchy.py:60`` read the wrong JSON path
   (``properties.details.parent`` instead of ``details.parent``) —
   every ``parent_id`` came out ``None``.
2. ``reconcile/proposer.py`` treated ``present_details`` as a dict when
   the producer emits a list — every ``displayName`` fell back to the
   MG ``name``, so the heuristic lost the ``"Sovereign Landing Zone"``
   signal.

This test also locks in the v0.10.0 **structural scoring** upgrade:
the heuristic now picks ``slz → alz`` (the MG whose two children match
the SLZ-intermediate shape) instead of the tenant-root GUID that the
old first-match-wins logic returned.

It pipes a sanitised real ``az account management-group show --expand``
response (``tests/fixtures/az/mg_show_tree.json``) through both stages
and asserts:

* every ``parent_id`` is populated (no flattening),
* ``displayName`` is preserved end-to-end,
* the heuristic proposer picks ``slz → alz``, ``platform → platform``,
  ``landingzones → None`` (ambiguous; LLM resolves).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from slz_readiness.discover import mg_hierarchy
from slz_readiness.reconcile.proposer import build_heuristic_proposal

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "az" / "mg_show_tree.json"


def _load_tree() -> dict[str, Any]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data.pop("_comment", None)
    return data


def test_discover_then_reconcile_over_real_cli_shape(monkeypatch) -> None:
    tree = _load_tree()
    # ``_list_mgs`` emulates ``az account management-group list`` — flat,
    # no parent info, just name + displayName.
    mgs_list = [
        {"name": name, "displayName": payload.get("displayName", name)}
        for name, payload in tree.items()
    ]

    monkeypatch.setattr(mg_hierarchy, "_list_mgs", lambda: mgs_list)
    monkeypatch.setattr(mg_hierarchy, "run_az", lambda *a, **k: mgs_list)
    monkeypatch.setattr(mg_hierarchy, "_show_mg", lambda name: tree.get(name))

    findings = mg_hierarchy.discover()
    state = findings[0]["observed_state"]

    # present_details is a list of {id, displayName, parent_id}.
    assert isinstance(state["present_details"], list)
    details_by_id = {d["id"]: d for d in state["present_details"]}

    # Every MG has a resolved parent_id (no flattening), except the root.
    root_id = "00000000-0000-0000-0000-000000000001"
    assert details_by_id[root_id]["parent_id"] is None
    assert details_by_id["customer-root"]["parent_id"] == root_id
    assert details_by_id["alz"]["parent_id"] == "customer-root"
    assert details_by_id["platform"]["parent_id"] == "alz"
    assert details_by_id["workloads"]["parent_id"] == "alz"

    # displayNames preserved — critical input for the reconcile heuristic.
    assert details_by_id["alz"]["displayName"] == "Sovereign Landing Zone"
    assert details_by_id[root_id]["displayName"] == "Tenant Root Group"

    # Feed findings into the heuristic proposer and check the mapping.
    proposal = build_heuristic_proposal({"findings": findings})

    # v0.10.0 structural scoring:
    #   - ``slz``: tenant-root excluded (parent_id is None). ``alz`` has
    #     displayName "Sovereign Landing Zone" (+1) and two children
    #     matching SLZ-intermediate shape (+3) → score 4; unique top.
    #   - ``platform``: substring (+1) + parent claimed as slz (+2) = 3.
    #   - ``landingzones``: ``alz`` already claimed; ``workloads`` has
    #     no substring hit on any landingzones pattern → null; the LLM
    #     resolves.
    #   - ``customer-root``: substring "root" matches ``slz`` (+1) but
    #     has only one child (``alz``) so no shape bonus; ``alz`` beats
    #     it 4-1 on score.
    assert proposal["slz"] == "alz"
    assert proposal["platform"] == "platform"
    assert proposal["landingzones"] is None

    # No MG should map to two roles:
    claimed = [v for v in proposal.values() if v is not None]
    assert len(claimed) == len(set(claimed))
