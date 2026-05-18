"""Phase 2-3 (skeleton align + vertex groups) UI walkthrough — recorded webm.

Walks phase_2 (skeleton_align) → phase_3 (vertex_groups) showing the shared
Phase23Stage. Verifies the stable-component-key change in StageRouter: the
stage should *not* remount when the agent advances from phase_2 to phase_3.
"""

from __future__ import annotations

import sys

from _walkthrough_common import URL, beat, push, record, try_fill
from playwright.sync_api import Page


def walk(page: Page) -> None:
    common = {"ts": 0, "phase": None, "state": "idle"}

    print("[/] Land on FallbackStage")
    page.goto(URL, wait_until="domcontentloaded")
    beat(page, 1500)

    try_fill(page, "input[placeholder*='model.fbx']", r"D:\demo\eku\eku.fbx", label="model path")
    try_fill(page, "input[placeholder*='textures']", r"D:\demo\eku\textures", label="texture dir")
    try_fill(page, "input[placeholder*='mod_root']", r"D:\demo\eku\mod_out", label="mod root")
    beat(page, 800)

    # ── Pretend Phase 1 already completed (so the stepper shows phase_1 done) ──
    print("[Phase 1] mark done so the stepper context reads naturally")
    push(page, "phase_started", {**common, "state": "running_phase", "phase": "phase_1", "index": 3, "total": 11})
    push(page, "phase_completed", {**common, "state": "running_phase", "phase": "phase_1", "index": 3, "total": 11})

    # ── Cross-fade into Phase 2 ───────────────────────────────────────────
    print("[Phase 2] start")
    push(page, "state", {**common, "state": "running_phase"})
    push(page, "phase_started", {**common, "state": "running_phase", "phase": "phase_2", "index": 4, "total": 11})
    beat(page, 2200)  # let the cross-fade land, show pending state

    print("[Phase 2] skeleton_align running")
    push(page, "tool_call", {
        **common, "state": "running_phase", "phase": "phase_2",
        "id": "tc_align_1", "name": "skeleton_align",
        "input": {
            "x_preset": "MMD",
            "y_preset": "怪猎荒野",
            "source_armature": "Armature.001",
            "target_armature": "MHWilds_Female Armature",
        },
    })
    beat(page, 2200)

    print("[Phase 2] skeleton_align ok")
    push(page, "tool_result", {
        **common, "state": "running_phase", "phase": "phase_2",
        "id": "tc_align_1", "name": "skeleton_align", "success": True,
        "summary": (
            "Snapped 87 source bones to MHWilds reference via MMD → 怪猎荒野 preset. "
            "Auto-unhid Armature.001 to make it selectable. 3 bones skipped per Y preset's skip_snap list."
        ),
    })
    beat(page, 2500)

    push(page, "phase_completed", {**common, "state": "running_phase", "phase": "phase_2", "index": 4, "total": 11})
    beat(page, 600)

    # ── Advance to Phase 3 — same stage component, no remount ──────────────
    print("[Phase 3] start (same stage instance — no cross-fade expected)")
    push(page, "phase_started", {**common, "state": "running_phase", "phase": "phase_3", "index": 5, "total": 11})
    beat(page, 1800)

    print("[Phase 3] vertex_groups running")
    push(page, "tool_call", {
        **common, "state": "running_phase", "phase": "phase_3",
        "id": "tc_vg_1", "name": "vertex_groups",
        "input": {
            "x_preset": "MMD",
            "mesh_objects": ["Body", "Hair", "Skirt", "Accessories"],
            "target_armature": "MHWilds_Female Armature",
        },
    })
    beat(page, 2400)

    print("[Phase 3] vertex_groups ok")
    push(page, "tool_result", {
        **common, "state": "running_phase", "phase": "phase_3",
        "id": "tc_vg_1", "name": "vertex_groups", "success": True,
        "summary": (
            "Merged into Body (4 source meshes joined). "
            "Renamed 138 vertex groups via modder.direct_convert. "
            "Re-parented to MHWilds_Female Armature; 42183 verts preserved."
        ),
    })
    beat(page, 3000)

    push(page, "phase_completed", {**common, "state": "await_confirm", "phase": "phase_3", "index": 5, "total": 11})

    print("[ChatStrip] surface assistant wrap-up")
    push(page, "message", {
        **common, "state": "await_confirm", "phase": "phase_3", "role": "assistant",
        "content": (
            "Phases 2 + 3 complete. Skeleton aligned and 4 source meshes merged into "
            "'Body' (138 vertex groups, 42k verts) under the MHWilds armature. "
            "Ready for Phase 4 (physics)."
        ),
    })
    push(page, "state", {**common, "state": "await_confirm"})
    beat(page, 1200)

    page.locator("[aria-controls='chatstrip-body']").first.click(timeout=3000)
    beat(page, 2200)


if __name__ == "__main__":
    sys.exit(record("phase23_walkthrough", walk))
