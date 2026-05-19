"""
Phase 2 — Skeleton Alignment (plan.md video 2).

Aligns the source model's skeleton to the target game skeleton using X/Y presets.
Must run AFTER Phase 1 (pose correction) — misaligned poses amplify bone offsets.

Operator: modder.universal_snap (骨架对齐 [X+Y, 双骨架])
  Requires:
    - Two ARMATURE objects selected: source (X) first, target (Y) Ctrl+clicked
    - context.active_object = target armature (Y)
    - Both import_preset_enum (X) and target_preset_enum (Y) loaded

Required params:
  x_preset         : str  — source model preset ("MMD" | "VRChat" | "终末地")
  source_armature  : str  — Blender object name of the source ARMATURE
  target_armature  : str  — Blender object name of the game ARMATURE (Y skeleton)
  y_preset         : str  — target game preset (default: "怪猎荒野")

Classification decision (agent loop, E17):
  The X preset selection IS the key classification — which source type is the model?
  Agent loop asks LLM, gets "MMD" / "VRChat" / "终末地", then calls this phase.
  Y preset is fixed to "怪猎荒野" for MVP.
"""

from __future__ import annotations

from typing import Any

from app.blender.client import BLENDER_SENTINEL, BlenderClient, BlenderError
from app.blender.state import SceneCache
from app.phases.base import (
    DEFAULT_Y_PRESET,
    X_PRESETS,
    Y_PRESETS,
    PhaseError,
    PhaseResult,
    PhaseTool,
    require_finished,
)

_OP = "modder.universal_snap"


class SkeletonAlign(PhaseTool):
    """Phase 2: X+Y preset-based skeleton alignment."""

    @property
    def name(self) -> str:
        return "skeleton_align"

    @property
    def phase_slot(self) -> str | None:
        return "phase_2"

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        return {
            "name": "skeleton_align",
            "description": (
                "Phase 2: Align MHWs reference skeleton bones to source model bone "
                "positions using the X+Y dual-armature preset operator. "
                "Run after pose_correction, before vertex_groups."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "x_preset": {
                        "type": "string",
                        "enum": ["MMD", "VRChat", "终末地"],
                        "description": "Source model type preset.",
                    },
                    "source_armature": {
                        "type": "string",
                        "description": "Blender ARMATURE object name for the source model.",
                    },
                    "target_armature": {
                        "type": "string",
                        "description": (
                            "Blender ARMATURE object name for the MHWs reference skeleton. "
                            "Always 'MHWilds_Female Armature' after setup_import_mhwilds — "
                            "use this fixed value without asking the user."
                        ),
                    },
                    "y_preset": {
                        "type": "string",
                        "enum": ["怪猎荒野"],
                        "description": "Target game preset. Always 怪猎荒野 for MHWs.",
                    },
                },
                "required": ["x_preset", "source_armature", "target_armature"],
            },
        }

    def run(
        self,
        client: BlenderClient,
        cache: SceneCache,
        params: dict,
    ) -> PhaseResult:
        # ── param validation ───────────────────────────────────────────────
        x_preset = params.get("x_preset", "")
        if x_preset not in X_PRESETS:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Unknown X preset {x_preset!r}. Valid: {sorted(X_PRESETS)}",
                    suggestion="Choose from MMD, VRChat, or 终末地.",
                )
            )

        y_preset = params.get("y_preset", DEFAULT_Y_PRESET)
        if y_preset not in Y_PRESETS:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message=f"Unknown Y preset {y_preset!r}. Valid: {sorted(Y_PRESETS)}",
                )
            )

        source_arm = params.get("source_armature", "")
        target_arm = params.get("target_armature", "")
        if not source_arm or not target_arm:
            return PhaseResult.fail(
                PhaseError(
                    category="precondition",
                    operator="",
                    message="Both 'source_armature' and 'target_armature' params are required.",
                )
            )

        # ── entry spot-check ───────────────────────────────────────────────
        state_before = cache.refresh()

        # ── execute ────────────────────────────────────────────────────────
        try:
            error = self._run_align(client, source_arm, target_arm, x_preset, y_preset)
        except BlenderError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="unexpected",
                    operator=_OP,
                    message="Blender returned an error during skeleton alignment.",
                    raw=str(exc),
                )
            )
        except OSError as exc:
            return PhaseResult.fail(
                PhaseError(
                    category="timeout",
                    operator=_OP,
                    message="Lost connection to Blender during skeleton alignment.",
                    raw=str(exc),
                )
            )

        if error is not None:
            return PhaseResult.fail(error)

        # ── exit cache update ──────────────────────────────────────────────
        state_after = cache.refresh()
        return PhaseResult.ok(state_before.diff(state_after))

    # ── private helpers ────────────────────────────────────────────────────

    def _run_align(
        self,
        client: BlenderClient,
        source_arm: str,
        target_arm: str,
        x_preset: str,
        y_preset: str,
    ) -> PhaseError | None:
        """
        Set presets, select armatures (source first, target active), run universal_snap.

        Selection order matters: source first, Ctrl+click target, target is active.
        Bones with skip_snap:true in Y preset are automatically skipped by the operator.
        """
        # NOTE on hidden armatures:
        #   modder.universal_snap requires both armatures SELECTED.  Hidden objects
        #   (either o.hide_viewport=True [outliner camera-off] or o.hide_get()=True
        #   [viewport H-key]) cannot be selected — select_set(True) is a silent
        #   no-op for hide_get()=True, and the operator then fails with a confusing
        #   generic error.  Auto-unhide both armatures before selecting; user can
        #   re-hide manually after.
        code = (
            f"import bpy\n"
            # Object existence checks
            f"src = bpy.data.objects.get({source_arm!r})\n"
            f"tgt = bpy.data.objects.get({target_arm!r})\n"
            f"missing = []\n"
            f"if src is None: missing.append({source_arm!r})\n"
            f"if tgt is None: missing.append({target_arm!r})\n"
            f"if missing:\n"
            f"    print({BLENDER_SENTINEL!r})\n"
            f"    print('PRECONDITION:objects_not_found:' + ','.join(missing))\n"
            f"else:\n"
            # Set presets
            f"    settings = bpy.context.scene.mhw_suite_settings\n"
            f"    settings.import_preset_enum = {(x_preset + '.json')!r}\n"
            f"    settings.target_preset_enum = {(y_preset + '.json')!r}\n"
            # Auto-unhide both armatures (and their containing collections) so
            # select_set + active assignment actually take effect.
            f"    unhidden = []\n"
            f"    for o in (src, tgt):\n"
            f"        if o.hide_viewport:\n"
            f"            o.hide_viewport = False\n"
            f"            unhidden.append(o.name + ':hide_viewport')\n"
            f"        if o.hide_get():\n"
            f"            o.hide_set(False)\n"
            f"            unhidden.append(o.name + ':hide_get')\n"
            # Select: source first, then target (active)
            f"    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"    bpy.ops.object.select_all(action='DESELECT')\n"
            f"    src.select_set(True)\n"
            f"    tgt.select_set(True)\n"
            f"    bpy.context.view_layer.objects.active = tgt\n"
            # Defensive post-select assertion — if select_set silently failed
            # (e.g. armature in a hidden collection), fail with an actionable
            # message rather than letting the operator produce a generic error.
            f"    not_selected = [o.name for o in (src, tgt) if not o.select_get()]\n"
            f"    if not_selected:\n"
            f"        print({BLENDER_SENTINEL!r})\n"
            f"        print('PRECONDITION:not_selectable:' + ','.join(not_selected))\n"
            f"    else:\n"
            # Run
            f"        ret = bpy.ops.{_OP}()\n"
            f"        print({BLENDER_SENTINEL!r})\n"
            f"        print('UNHIDDEN:' + (','.join(unhidden) if unhidden else 'none'))\n"
            f"        print(ret)\n"
        )
        lines = client.execute_and_extract(code)
        if lines and lines[0].startswith("PRECONDITION:objects_not_found:"):
            missing = lines[0].split(":", 2)[-1] if ":" in lines[0] else "unknown"
            return PhaseError(
                category="precondition",
                operator=_OP,
                message=f"Armature object(s) not found in scene: {missing}",
                suggestion=(
                    "Open Blender's Outliner and confirm both armature names. "
                    "The target game skeleton must be imported into the scene first."
                ),
            )
        if lines and lines[0].startswith("PRECONDITION:not_selectable:"):
            names = lines[0].split(":", 2)[-1] if ":" in lines[0] else "unknown"
            return PhaseError(
                category="precondition",
                operator=_OP,
                message=f"Armature(s) not selectable even after auto-unhide: {names}",
                suggestion=(
                    "The armature is likely inside a hidden / excluded Collection, "
                    "or has hide_select=True.  In Blender Outliner: ensure both "
                    "armatures' containing collections are enabled (not greyed out), "
                    "and remove any selection-disable arrow icon on the armature."
                ),
            )
        # Strip the UNHIDDEN trace line before passing to require_finished()
        # so operator success detection isn't thrown off by the diagnostic note.
        unhidden_note = next((line for line in lines if line.startswith("UNHIDDEN:")), None)
        operator_lines = [line for line in lines if not line.startswith("UNHIDDEN:")]
        result = require_finished(operator_lines, _OP)
        if result is None and unhidden_note and unhidden_note != "UNHIDDEN:none":
            # Operator succeeded; surface the auto-unhide as a debug note via
            # logging.  Not a failure — phase still returns ok.  TODO: wire to
            # state_diff if we add a "warnings" field.
            pass
        return result
