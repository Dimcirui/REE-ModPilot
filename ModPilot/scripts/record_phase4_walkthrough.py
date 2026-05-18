"""Phase 4 (physics) UI walkthrough — recorded webm.

Covers all three sub-phases hosted by Phase4Stage:
  phase_35  — physics_transplant (bone graft)
  phase_4a  — physics_classification + ClassificationWidget overlay
  phase_4b  — physics_chains
"""

from __future__ import annotations

import sys

from _walkthrough_common import URL, beat, push, record, try_fill
from playwright.sync_api import Page


def _mock_chains() -> list[dict]:
    return [
        {
            "name": "hair_001",
            "role": "root",
            "depth": 0,
            "guessed_nature": "hair",
            "group": "hair",
            "suggested_type": "hair_short",
            "suggest_merge": False,
        },
        {
            "name": "hair_002",
            "role": "child",
            "depth": 1,
            "parent": "hair_001",
            "guessed_nature": "hair",
            "group": "hair",
            "suggested_type": "hair_short",
            "suggest_merge": True,
        },
        {
            "name": "skirt_front_L",
            "role": "root",
            "depth": 0,
            "guessed_nature": "cloth",
            "group": "cloth",
            "suggested_type": "skirt",
            "suggest_merge": False,
        },
        {
            "name": "ribbon_back",
            "role": "root",
            "depth": 0,
            "guessed_nature": "ribbon",
            "group": "ribbon",
            "suggested_type": "ribbon",
            "suggest_merge": False,
        },
    ]


def walk(page: Page) -> None:
    common = {"ts": 0, "phase": None, "state": "idle"}

    print("[/] Land on FallbackStage")
    page.goto(URL, wait_until="domcontentloaded")
    beat(page, 1500)

    try_fill(page, "input[placeholder*='model.fbx']", r"D:\demo\eku\eku.fbx", label="model path")
    try_fill(page, "input[placeholder*='mod_root']", r"D:\demo\eku\mod_out", label="mod root")
    beat(page, 600)

    # Mark phases 1-3 done so the user lands naturally in Phase 4
    for idx, ph in enumerate(["phase_1", "phase_2", "phase_3"], start=3):
        push(page, "phase_started", {**common, "state": "running_phase", "phase": ph, "index": idx, "total": 11})
        push(page, "phase_completed", {**common, "state": "running_phase", "phase": ph, "index": idx, "total": 11})

    # ── Phase 3.5: physics bone transplant ─────────────────────────────────
    print("[Phase 3.5] physics_transplant")
    push(page, "state", {**common, "state": "running_phase"})
    push(page, "phase_started", {**common, "state": "running_phase", "phase": "phase_35", "index": 6, "total": 11})
    beat(page, 2000)  # let the cross-fade settle

    push(page, "tool_call", {
        **common, "state": "running_phase", "phase": "phase_35",
        "id": "tc_graft", "name": "physics_transplant",
        "input": {
            "source_armature": "Armature.001",
            "target_armature": "MHWilds_Female Armature",
            "x_preset": "MMD",
        },
    })
    beat(page, 1800)

    push(page, "tool_result", {
        **common, "state": "running_phase", "phase": "phase_35",
        "id": "tc_graft", "name": "physics_transplant", "success": True,
        "summary": "Grafted 24 physics bones transplanted onto MHWilds_Female Armature via modder.smart_graft.",
    })
    beat(page, 2200)
    push(page, "phase_completed", {**common, "state": "running_phase", "phase": "phase_35", "index": 6, "total": 11})

    # ── Phase 4A: classification ──────────────────────────────────────────
    print("[Phase 4A] physics_classification + widget")
    push(page, "phase_started", {**common, "state": "running_phase", "phase": "phase_4a", "index": 7, "total": 11})
    beat(page, 800)

    push(page, "tool_call", {
        **common, "state": "running_phase", "phase": "phase_4a",
        "id": "tc_classify", "name": "physics_classification",
        "input": {"target_armature": "MHWilds_Female Armature"},
    })
    beat(page, 1700)

    push(page, "tool_result", {
        **common, "state": "running_phase", "phase": "phase_4a",
        "id": "tc_classify", "name": "physics_classification", "success": True,
        "summary": "Inspected 4 chain groups across 12 bones (hair, cloth, ribbon).",
    })
    beat(page, 600)

    # Open the classification widget (lands as an overlay on the viewport)
    push(page, "widget_classification", {
        **common, "state": "await_confirm", "phase": "phase_4a",
        "chains": _mock_chains(),
        "inferred_types": ["hair_short", "skirt", "ribbon"],
    })
    push(page, "state", {**common, "state": "await_confirm"})
    beat(page, 3200)  # let the user "see" the widget

    # User submits → widget consumed
    push(page, "tool_call", {
        **common, "state": "running_phase", "phase": "phase_4b",
        "id": "tc_chains", "name": "physics_chains",
        "input": {
            "chain_collection": "Chains.001",
            "chains": [
                {"name": "hair_001", "inferred_type": "hair_short"},
                {"name": "hair_002", "inferred_type": "hair_short", "merge": True},
                {"name": "skirt_front_L", "inferred_type": "skirt"},
                {"name": "ribbon_back", "inferred_type": "ribbon"},
            ],
        },
    })
    beat(page, 600)
    push(page, "state", {**common, "state": "running_phase"})
    push(page, "phase_completed", {**common, "state": "running_phase", "phase": "phase_4a", "index": 7, "total": 11})

    # ── Phase 4B: chain creation ──────────────────────────────────────────
    print("[Phase 4B] physics_chains running")
    push(page, "phase_started", {**common, "state": "running_phase", "phase": "phase_4b", "index": 8, "total": 11})
    beat(page, 2000)

    push(page, "tool_result", {
        **common, "state": "running_phase", "phase": "phase_4b",
        "id": "tc_chains", "name": "physics_chains", "success": True,
        "summary": "Created 3 chain groups (hair, skirt, ribbon) — 12 chains total written to RE Chain settings.",
    })
    beat(page, 1500)

    push(page, "tool_call", {
        **common, "state": "running_phase", "phase": "phase_4b",
        "id": "tc_adjust", "name": "physics_adjust",
        "input": {"chain_collection": "Chains.001"},
    })
    beat(page, 1500)
    push(page, "tool_result", {
        **common, "state": "running_phase", "phase": "phase_4b",
        "id": "tc_adjust", "name": "physics_adjust", "success": True,
        "summary": "Applied physics_presets.json params to all 3 chain settings.",
    })
    beat(page, 2400)

    push(page, "phase_completed", {**common, "state": "await_confirm", "phase": "phase_4b", "index": 8, "total": 11})
    push(page, "state", {**common, "state": "await_confirm"})
    beat(page, 1500)


if __name__ == "__main__":
    sys.exit(record("phase4_walkthrough", walk))
