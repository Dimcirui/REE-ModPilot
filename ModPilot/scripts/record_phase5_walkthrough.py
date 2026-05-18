"""Phase 5 (materials) UI walkthrough — recorded webm.

Walks consolidate → inspect → MaterialWidget (canvas swap) → submit → setup → generate.
"""

from __future__ import annotations

import sys

from _walkthrough_common import URL, beat, push, record, try_fill
from playwright.sync_api import Page


def _existing_connections() -> dict:
    return {
        "Body": {"Base Color": "C:/demo/textures/body_diff.png"},
        "Hair": {"Base Color": "connected_no_image"},
        "Eye": {},
    }


def _suggestions() -> dict:
    return {
        "Body": {
            "Base Color": "C:/demo/textures/body_diff.png",
            "Roughness": "C:/demo/textures/body_rough.png",
            "Normal": "C:/demo/textures/body_normal.png",
        },
        "Hair": {
            "Base Color": "C:/demo/textures/hair_diff.png",
            "Alpha": "C:/demo/textures/hair_alpha.png",
        },
        "Eye": {"Base Color": "C:/demo/textures/eye_diff.png"},
    }


def walk(page: Page) -> None:
    common = {"ts": 0, "phase": None, "state": "idle"}

    print("[/] Land on FallbackStage")
    page.goto(URL, wait_until="domcontentloaded")
    beat(page, 1400)

    try_fill(page, "input[placeholder*='model.fbx']", r"D:\demo\eku\eku.fbx", label="model path")
    try_fill(page, "input[placeholder*='textures']", r"D:\demo\eku\textures", label="texture dir")
    try_fill(page, "input[placeholder*='mod_root']", r"D:\demo\eku\mod_out", label="mod root")
    beat(page, 500)

    # Mark earlier phases done so the stepper context reads naturally.
    for idx, ph in enumerate(
        ["phase_1", "phase_2", "phase_3", "phase_35", "phase_4a", "phase_4b"], start=3
    ):
        push(page, "phase_started", {**common, "state": "running_phase", "phase": ph, "index": idx, "total": 11})
        push(page, "phase_completed", {**common, "state": "running_phase", "phase": ph, "index": idx, "total": 11})

    # ── Enter Phase 5 ─────────────────────────────────────────────────────
    print("[Phase 5] start")
    push(page, "state", {**common, "state": "running_phase"})
    push(page, "phase_started", {**common, "state": "running_phase", "phase": "phase_5", "index": 9, "total": 11})
    beat(page, 2200)  # cross-fade settles, Phase5Stage shows pipeline checklist

    # ── material_consolidate ──────────────────────────────────────────────
    print("[Phase 5] material_consolidate")
    push(page, "tool_call", {
        **common, "state": "running_phase", "phase": "phase_5",
        "id": "tc_consolidate", "name": "material_consolidate",
        "input": {"mesh_collection": "MHWilds_Female.mesh"},
    })
    beat(page, 1500)
    push(page, "tool_result", {
        **common, "state": "running_phase", "phase": "phase_5",
        "id": "tc_consolidate", "name": "material_consolidate", "success": True,
        "summary": "Consolidated 12 material slots into 3 materials.",
    })
    beat(page, 1500)

    # ── material_inspect ──────────────────────────────────────────────────
    print("[Phase 5] material_inspect")
    push(page, "tool_call", {
        **common, "state": "running_phase", "phase": "phase_5",
        "id": "tc_inspect", "name": "material_inspect",
        "input": {"mesh_collection": "MHWilds_Female.mesh"},
    })
    beat(page, 1500)
    push(page, "tool_result", {
        **common, "state": "running_phase", "phase": "phase_5",
        "id": "tc_inspect", "name": "material_inspect", "success": True,
        "summary": "Inspected 3 materials: Body, Hair, Eye.",
    })
    beat(page, 1500)

    # ── MaterialWidget swap onto canvas ──────────────────────────────────
    print("[Phase 5] MaterialWidget swap")
    push(page, "widget_material", {
        **common, "state": "await_confirm", "phase": "phase_5",
        "materials": ["Body", "Hair", "Eye"],
        "existing_connections": _existing_connections(),
        "texture_files": [
            "C:/demo/textures/body_diff.png",
            "C:/demo/textures/body_rough.png",
            "C:/demo/textures/body_normal.png",
            "C:/demo/textures/hair_diff.png",
            "C:/demo/textures/hair_alpha.png",
            "C:/demo/textures/eye_diff.png",
        ],
        "suggestions": _suggestions(),
    })
    push(page, "state", {**common, "state": "await_confirm"})
    beat(page, 3800)  # let viewer scan widget + sidebar hint

    # User submits → material_setup tool call clears the widget (WIDGET_CLEAR_TOOLS)
    push(page, "tool_call", {
        **common, "state": "running_phase", "phase": "phase_5",
        "id": "tc_setup", "name": "material_setup",
        "input": {"mappings": "[6 slots × 3 materials]"},
    })
    push(page, "state", {**common, "state": "running_phase"})
    beat(page, 1400)  # canvas swaps back to viewport

    push(page, "tool_result", {
        **common, "state": "running_phase", "phase": "phase_5",
        "id": "tc_setup", "name": "material_setup", "success": True,
        "summary": "Material setup applied to 3 materials.",
    })
    beat(page, 1000)

    # ── material_generate ────────────────────────────────────────────────
    push(page, "tool_call", {
        **common, "state": "running_phase", "phase": "phase_5",
        "id": "tc_gen", "name": "material_generate",
        "input": {"mesh_collection": "MHWilds_Female.mesh"},
    })
    beat(page, 1600)
    push(page, "tool_result", {
        **common, "state": "running_phase", "phase": "phase_5",
        "id": "tc_gen", "name": "material_generate", "success": True,
        "summary": "Generated MDF2 nodes. 14 textures assigned, 3 materials wired with full PBR slots.",
    })
    beat(page, 2400)

    push(page, "phase_completed", {**common, "state": "await_confirm", "phase": "phase_5", "index": 9, "total": 11})
    push(page, "state", {**common, "state": "await_confirm"})
    beat(page, 1500)


if __name__ == "__main__":
    sys.exit(record("phase5_walkthrough", walk))
