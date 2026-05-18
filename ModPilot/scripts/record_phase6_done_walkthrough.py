"""Phase 6 (batch_export) + Done UI walkthrough — recorded webm.

Shows the export tile (armor id chip, 5 part chips, file/size stats, natives
path), then transitions into the DoneStage when loopState flips to 'done'.
"""

from __future__ import annotations

import sys

from _walkthrough_common import URL, beat, push, record, try_fill
from playwright.sync_api import Page


def walk(page: Page) -> None:
    common = {"ts": 0, "phase": None, "state": "idle"}

    print("[/] Land on FallbackStage")
    page.goto(URL, wait_until="domcontentloaded")
    beat(page, 1400)

    try_fill(page, "input[placeholder*='model.fbx']", r"D:\demo\eku\eku.fbx", label="model path")
    try_fill(page, "input[placeholder*='mod_root']", r"D:\demo\eku\mod_out", label="mod root")
    beat(page, 500)

    # Mark all earlier phases done so we land in phase_6 naturally.
    for idx, ph in enumerate(
        ["phase_1", "phase_2", "phase_3", "phase_35", "phase_4a", "phase_4b", "phase_5"], start=3
    ):
        push(page, "phase_started", {**common, "state": "running_phase", "phase": ph, "index": idx, "total": 11})
        push(page, "phase_completed", {**common, "state": "running_phase", "phase": ph, "index": idx, "total": 11})

    # ── Phase 6 start ─────────────────────────────────────────────────────
    print("[Phase 6] start")
    push(page, "state", {**common, "state": "running_phase"})
    push(page, "phase_started", {**common, "state": "running_phase", "phase": "phase_6", "index": 10, "total": 11})
    beat(page, 2400)  # cross-fade lands, pending state visible

    print("[Phase 6] batch_export running")
    push(page, "tool_call", {
        **common, "state": "running_phase", "phase": "phase_6",
        "id": "tc_export", "name": "batch_export",
        "input": {
            "armor_id": "pl034",
            "armor_variant": "ff",
            "target_parts": ["1", "2", "4"],
            "mesh_collection": "MHWilds_Female.mesh",
            "mdf2_collection": "MHWilds_Female.mdf2",
            "chain2_collection": "Chains.001",
            "target_armature": "MHWilds_Female Armature",
            "fbxskel_name": "ch03_000_9000",
            "natives_root": "D:/demo/eku/mod_out",
        },
    })
    beat(page, 3000)  # show "running" tile + variant chip

    print("[Phase 6] batch_export ok")
    push(page, "tool_result", {
        **common, "state": "running_phase", "phase": "phase_6",
        "id": "tc_export", "name": "batch_export", "success": True,
        "summary": (
            "Exported 14 files (4.27 MB written) to "
            "natives_root=D:/demo/eku/mod_out/natives/STM/GameDesign/ "
            "(Arms / Body / Legs bound; Helmet / Waist as empty placeholders). "
            "BoneSystem export ran via ch03_000_9000 fbxskel."
        ),
    })
    beat(page, 3500)  # let the stats / parts / path land

    push(page, "phase_completed", {**common, "state": "running_phase", "phase": "phase_6", "index": 10, "total": 11})
    beat(page, 600)

    # ── Done — DoneStage takes over via StageRouter priority ──────────────
    print("[Done] flip loopState → done, DoneStage cross-fades in")
    push(page, "message", {
        **common, "state": "done", "phase": "phase_6", "role": "assistant",
        "content": (
            "All phases complete — mod pl034 written to D:/demo/eku/mod_out/natives. "
            "Test in-game and check physics on hair/skirt at extreme poses."
        ),
    })
    push(page, "state", {**common, "state": "done"})
    push(page, "done", {**common, "state": "done", "reply": "", "session_id": "demo"})
    beat(page, 4500)  # animated check ring + stats + path card

    # Expand chat strip to reveal the wrap-up message
    page.locator("[aria-controls='chatstrip-body']").first.click(timeout=3000)
    beat(page, 2500)


if __name__ == "__main__":
    sys.exit(record("phase6_done_walkthrough", walk))
